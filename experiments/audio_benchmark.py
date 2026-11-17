"""Reproducible, *human-judged* music-to-music evaluation of acoustic backends.

Example: python -m experiments.audio_benchmark --dataset examples/my_judgments.json \\
             --methods dsp clap --clap-checkpoint C:/models/music_clap.pt --out report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

import numpy as np

from experiments.audio_models import CLAP, DSP, Essentia, BackendUnavailable
from engine import SUPPORTED_EXTENSIONS


class DatasetError(ValueError):
    pass


def load_dataset(manifest: Path) -> list[dict]:
    """Use only readable local audio paths and explicit 0..3 human judgments."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    queries = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(queries, list) or not queries:
        raise DatasetError("Dataset must have at least one human-judged query")
    if len(queries) > 1000:
        raise DatasetError("Dataset has too many queries")
    base = manifest.resolve().parent

    def audio_path(value):
        if not isinstance(value, str) or not value.strip():
            raise DatasetError("Audio paths must be nonempty strings")
        path = (base / value).resolve()
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise DatasetError(f"Missing/unsupported audio: {value}")
        return path

    seen_queries = set()
    resolved = []
    for query in queries:
        if not isinstance(query, dict):
            raise DatasetError("Each query must be an object")
        identifier = str(query.get("id", "")).strip()
        if not identifier or identifier in seen_queries:
            raise DatasetError("Query IDs must be unique and nonempty")
        seen_queries.add(identifier)
        reference = audio_path(query.get("reference"))
        entries = query.get("candidates")
        if not isinstance(entries, list) or len(entries) < 2 or len(entries) > 10000:
            raise DatasetError("Each query needs between 2 and 10000 judged candidates")
        candidates = []
        ids = set()
        for item in entries:
            if not isinstance(item, dict):
                raise DatasetError("Each candidate must be an object")
            candidate_id = str(item.get("id", "")).strip()
            relevance = item.get("relevance")
            if not candidate_id or candidate_id in ids:
                raise DatasetError("Candidate IDs must be unique within each query")
            if type(relevance) is not int or not 0 <= relevance <= 3:
                raise DatasetError("Human relevance must be an integer from 0 to 3")
            ids.add(candidate_id)
            path = audio_path(item.get("path"))
            if path == reference:
                raise DatasetError("The reference file must not also be a candidate")
            candidates.append({"id": candidate_id, "path": path, "relevance": relevance})
        resolved.append({"id": identifier, "reference": reference, "candidates": candidates})
    return resolved


def ndcg_at_k(ranked: list[int], k: int) -> float:
    def dcg(items):
        return sum((2 ** label - 1) / math.log2(i + 2) for i, label in enumerate(items[:k]))
    ideal = dcg(sorted(ranked, reverse=True))
    return dcg(ranked) / ideal if ideal else 0.0


def precision_at_k(ranked: list[int], k: int, threshold: int = 2) -> float:
    if not ranked or k < 1:
        return 0.0
    return sum(label >= threshold for label in ranked[:k]) / min(k, len(ranked))


def backend_cache_key(path: Path, backend, model_path: Path | None) -> str:
    stat = path.stat()
    model = "none"
    if model_path is not None:
        model_stat = model_path.stat()
        model = f"{model_path.resolve()}|{model_stat.st_size}|{model_stat.st_mtime_ns}"
    identity = f"embedding-eval-1|{backend.name}|{backend.seconds}|{model}|{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _atomic_json(path: Path, document: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf8", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            json.dump(document, f, indent=2, allow_nan=False)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
    os.replace(temp, path)


def evaluate(queries: list[dict], backends: dict, *, cache_dir: Path | None = None, models: dict | None = None, k: int = 3) -> dict:
    """No silent fallback; unavailable models are recorded, never reported as zero accuracy."""
    models = models or {}
    report = {"schema": 1, "judgment_scale": "0..3 human-assigned", "k": k, "methods": {}}
    for name, backend in backends.items():
        if isinstance(backend, Exception):
            report["methods"][name] = {"status": "unavailable", "reason": str(backend)}
            continue
        cache_file = (cache_dir / f"{name}.json") if cache_dir else None
        cache = {}
        if cache_file and cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text(encoding="utf8"))
            except (OSError, ValueError):
                cache = {}
        scores = []
        findings = []
        reused = 0
        started = time.perf_counter()
        errors = []

        def get(path):
            nonlocal reused
            model = models.get(name)
            key = backend_cache_key(path, backend, model)
            if key in cache:
                reused += 1
                return cache[key]
            obj = backend.extract(path)
            # Store DSP dictionaries or embeddings as a small serializable list.
            values = obj.tolist() if isinstance(obj, np.ndarray) else obj
            cache[key] = values
            return values

        for query in queries:
            try:
                ref = get(query["reference"])
                ranked = sorted([{"id": cand["id"], "score": float(backend.similarity(ref, get(cand["path"]))),
                                   "relevance": cand["relevance"]} for cand in query["candidates"]],
                                key=lambda x: (-x["score"], x["id"]))
                if any(not math.isfinite(x["score"]) for x in ranked):
                    raise ValueError("Invalid non-finite similarity score")
                labels = [x["relevance"] for x in ranked]
                metrics = {"ndcg_at_k": round(ndcg_at_k(labels, k), 5),
                           "precision_at_k": round(precision_at_k(labels, k), 5)}
                scores.append(metrics)
                findings.append({"query_id": query["id"], "rankings": ranked, "metrics": metrics})
            except (ValueError, OSError, RuntimeError, ImportError) as exc:
                errors.append(f"{query['id']}: {type(exc).__name__}: {exc}")
        if cache_file:
            _atomic_json(cache_file, cache)
        report["methods"][name] = {
            "status": "complete" if not errors else "partial" if scores else "failed",
            "queries_evaluated": len(scores), "queries_failed": len(errors),
            "mean_ndcg_at_k": round(sum(s["ndcg_at_k"] for s in scores) / len(scores), 5) if scores else None,
            "mean_precision_at_k": round(sum(s["precision_at_k"] for s in scores) / len(scores), 5) if scores else None,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "cache_hits": reused, "queries": findings, "errors": errors,
        }
    return report


def construct_backends(names: list[str], *, clap_checkpoint=None, essentia_graph=None, seconds: int = 50) -> tuple[dict, dict]:
    backends = {}
    paths = {"clap": Path(clap_checkpoint).expanduser() if clap_checkpoint else None,
             "essentia": Path(essentia_graph).expanduser() if essentia_graph else None}
    for name in names:
        try:
            if name == "dsp":
                backends[name] = DSP(seconds)
            elif name == "clap":
                backends[name] = CLAP(paths[name] or Path("missing-clap-checkpoint"), seconds)
            elif name == "essentia":
                backends[name] = Essentia(paths[name] or Path("missing-essentia-model"), seconds)
            else:
                raise DatasetError(f"Unsupported method: {name}")
        except BackendUnavailable as exc:
            backends[name] = exc
    return backends, paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare genuine audio-similarity systems using your human judgments")
    parser.add_argument("--dataset", type=Path, required=True, help="JSON of audio paths and 0..3 human relevance ratings")
    parser.add_argument("--methods", default="dsp,clap,essentia", help="Comma-separated: dsp,clap,essentia")
    parser.add_argument("--clap-checkpoint", help="Path to a trusted local LAION CLAP music checkpoint")
    parser.add_argument("--essentia-graph", help="Path to a local Essentia Discogs track embedding .pb model")
    parser.add_argument("--seconds", type=int, default=50)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/embedding-benchmark"))
    parser.add_argument("--out", type=Path, default=Path("benchmark-report.json"))
    args = parser.parse_args(argv)
    if not 2 <= args.seconds <= 120 or args.k < 1:
        parser.error("--seconds must be 2..120 and --k must be at least 1")
    names = [x.strip() for x in args.methods.split(",")]
    if len(names) != len(set(names)) or not names or any(n not in {"dsp", "clap", "essentia"} for n in names):
        parser.error("--methods must contain unique dsp/clap/essentia names")
    queries = load_dataset(args.dataset)
    backends, paths = construct_backends(names, clap_checkpoint=args.clap_checkpoint, essentia_graph=args.essentia_graph, seconds=args.seconds)
    summary = evaluate(queries, backends, cache_dir=args.cache_dir, models=paths, k=args.k)
    _atomic_json(args.out, summary)
    print(json.dumps({"report": str(args.out), "methods": {key: {
        "status": value["status"], "mean_ndcg_at_k": value.get("mean_ndcg_at_k"),
        "mean_precision_at_k": value.get("mean_precision_at_k"), "queries_evaluated": value.get("queries_evaluated", 0),
        "reason": value.get("reason"),
    } for key, value in summary["methods"].items()}}, indent=2))
    if not any(value["status"] == "complete" for value in summary["methods"].values()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())