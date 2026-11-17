"""Bounded parallel acoustic scoring of licensed, downloadable catalogue candidates.

Downloads use up to four threads (network I/O); DSP feature extraction uses up
 to two processes (CPU).  Audio remains in a TemporaryDirectory and is removed
immediately after extraction.  Only fingerprints are cached between searches.

The yielded events are consumed on the Streamlit main thread: worker processes
never access Streamlit state or its UI.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterator

from engine import analyse, compare
from downloaders import download_track_audio
from urllib.parse import urlsplit

CACHE_SCHEMA = 1
FINGERPRINT_VERSION = "dsp50-v2"
MAX_CACHE_ENTRIES = 1200


def _key(track: dict) -> str:
    identity = f"{FINGERPRINT_VERSION}:{track.get('id', '')}:{track.get('audio_url', '')}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _read_cache(file: Path) -> dict[str, dict]:
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
        if document.get("schema") != CACHE_SCHEMA or not isinstance(document.get("tracks"), dict):
            return {}
        return document["tracks"]
    except (OSError, ValueError, AttributeError):
        return {}


def _save_cache(file: Path, tracks: dict) -> None:
    """Atomic write; never put raw audio or API credentials in the cache."""
    file.parent.mkdir(parents=True, exist_ok=True)
    # Retain the latest results; the dict is insertion-ordered in Python 3.7+.
    recent = dict(list(tracks.items())[-MAX_CACHE_ENTRIES:])
    encoded = json.dumps({"schema": CACHE_SCHEMA, "tracks": recent}, separators=(",", ":"))
    fd, temporary = tempfile.mkstemp(dir=file.parent, prefix="fingerprints-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(encoded)
        os.replace(temporary, file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _extract_features(path: Path) -> dict:
    """Module-level function so Windows spawned worker processes can import it."""
    return analyse(path, max_seconds=50)


def scan_candidates(
    candidates: list[dict], reference: dict, weights: dict,
    cache_file: Path, *, download_workers: int = 4, analysis_workers: int = 2,
    downloader: Callable = download_track_audio,
    analyser: Callable = _extract_features,
    analysis_executor_cls: type = ProcessPoolExecutor,
) -> Iterator[dict[str, Any]]:
    """Yield each completed scored track or skipped track as soon as it finishes.

    The pipeline bounds total in-flight and queued tracks to the sum of the
    two worker pools, preventing downloads from outrunning slower analysis.
    The caller can update its UI after
    every event; final rankings are computed from all successful results.

    For deterministic tests, supply a picklable fake analyser and substitute
    ThreadPoolExecutor for analysis_executor_cls.  No external network calls
    occur except through downloader.
    """
    if not 1 <= download_workers <= 8 or not 1 <= analysis_workers <= 4:
        raise ValueError("Worker count out of bounds")
    total = len(candidates)
    if not total:
        return
    cache_file = Path(cache_file)
    cached = _read_cache(cache_file)
    completed = 0
    sources = iter(enumerate(candidates))

    with tempfile.TemporaryDirectory(prefix="vibe-scan-") as temp_dir:
        folder = Path(temp_dir)
        with ThreadPoolExecutor(max_workers=download_workers) as download_pool, \
                analysis_executor_cls(max_workers=analysis_workers) as analysis_pool:
            downloads = {}  # future -> (track, dest)
            analyses = {}   # future -> (track, dest)
            ready = deque() # downloaded but not yet analysed

            # Cached descriptors can be scored immediately, without network I/O.
            uncached = []
            for idx, track in sources:
                features = cached.get(_key(track))
                if isinstance(features, dict):
                    try:
                        scored = {**track, **compare(reference, features, weights),
                                  "bpm": features["bpm"], "cached": True}
                        completed += 1
                        yield {"completed": completed, "total": total, "match": scored, "error": None}
                        continue
                    except (KeyError, ValueError, TypeError):
                        pass
                uncached.append((idx, track))
            backlog = iter(uncached)
            exhausted = False
            dirty_cache = 0
            queued_limit = download_workers + analysis_workers

            while downloads or analyses or ready or not exhausted:
                # Do not produce unbounded temp files when DSP is slower than I/O.
                while not exhausted and len(downloads) < download_workers and \
                        len(downloads) + len(analyses) + len(ready) < queued_limit:
                    try:
                        idx, track = next(backlog)
                    except StopIteration:
                        exhausted = True
                        break
                    suffix = Path(urlsplit(str(track.get("audio_url", ""))).path).suffix.lower()
                    if suffix not in {".mp3", ".wav", ".ogg", ".oga", ".flac", ".m4a", ".opus"}:
                        suffix = ".mp3"
                    dest = folder / f"candidate_{idx}{suffix}"
                    fut = download_pool.submit(downloader, track, dest)
                    downloads[fut] = (track, dest)

                while ready and len(analyses) < analysis_workers:
                    track, path = ready.popleft()
                    fut = analysis_pool.submit(analyser, path)
                    analyses[fut] = (track, path)

                pending = set(downloads) | set(analyses)
                if not pending:
                    continue
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    if future in downloads:
                        track, path = downloads.pop(future)
                        try:
                            resolved_path = future.result()
                            ready.append((track, Path(resolved_path)))
                        except Exception as exc:
                            path.unlink(missing_ok=True)
                            completed += 1
                            yield {"completed": completed, "total": total,
                                   "match": None, "error": f"{track['artist']} – {track['title']}: {exc}"}
                    else:
                        track, path = analyses.pop(future)
                        try:
                            features = future.result()
                            result = {**track, **compare(reference, features, weights),
                                      "bpm": features["bpm"], "cached": False}
                            cached[_key(track)] = features
                            dirty_cache += 1
                            # Save periodically, not on every candidate; this avoids
                            # repeatedly rewriting an increasing cache during scans.
                            if dirty_cache >= 4:
                                try:
                                    _save_cache(cache_file, cached)
                                    dirty_cache = 0
                                except OSError:
                                    pass  # Cache failures cannot hide a valid match.
                            completed += 1
                            yield {"completed": completed, "total": total,
                                   "match": result, "error": None}
                        except Exception as exc:
                            completed += 1
                            yield {"completed": completed, "total": total,
                                   "match": None, "error": f"{track['artist']} – {track['title']}: {exc}"}
                        finally:
                            path.unlink(missing_ok=True)

            if dirty_cache:
                try:
                    _save_cache(cache_file, cached)
                except OSError:
                    pass
