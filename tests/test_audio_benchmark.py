"""Offline contract/smoke tests. Not human-rated real music accuracy evidence."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from experiments.audio_models import (
    CLAP, DSP, Essentia, BackendUnavailable, cosine_similarity, normalized_pool,
)
from experiments.audio_benchmark import (
    DatasetError, construct_backends, evaluate, load_dataset, ndcg_at_k,
    precision_at_k, backend_cache_key,
)


class FakeEmbeddingBackend:
    name = "fake_embeddings"
    seconds = 50

    def __init__(self):
        self.calls = 0

    def extract(self, path):
        self.calls += 1
        if path.name.startswith("reference"):
            return np.array([1.0, 0])
        if path.name.startswith("near"):
            return np.array([1.0, 0.1])
        return np.array([0, 1.0])

    def similarity(self, ref, cand):
        return cosine_similarity(ref, cand)


class FakeClap:
    def __init__(self):
        self.seen = []
    def get_audio_embedding_from_data(self, x, use_tensor=False):
        self.seen.append((x.shape, use_tensor))
        return np.stack([np.array([1.0, 0.1]) for _ in x])


class FakeEssentia:
    def __init__(self):
        self.input_size = None
    def __call__(self, x):
        self.input_size = x.shape[0]
        return np.asarray([[2., 0.], [1.0, 0.0]])


def sine(path: Path, freq=220, seconds=3):
    sr = 22050
    x = (0.25 * np.sin(2 * np.pi * freq * np.arange(seconds * sr) / sr)).astype(np.float32)
    sf.write(path, x, sr)


class AudioModels(unittest.TestCase):
    def test_pool_unit_length_and_zero_rejected(self):
        p = normalized_pool(np.asarray([[2., 0.], [1., 0.]]))
        self.assertAlmostEqual(float(np.linalg.norm(p)), 1.0)
        self.assertRaises(ValueError, normalized_pool, [[0., 0.]])
        self.assertRaises(ValueError, normalized_pool, [[np.nan, 1.]])

    def test_cosine_sign_and_validation(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 1]), 0.0)
        self.assertAlmostEqual(cosine_similarity([1, 0], [-1, 0]), -1.0)
        self.assertRaises(ValueError, cosine_similarity, [1, 0], [1, 2, 3])

    def test_clap_receives_real_audio_samples_and_no_download(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "reference.wav"
            model_file = Path(d) / "trusted.pt"
            model_file.write_bytes(b"test-only")
            sine(path, seconds=3)
            fake = FakeClap()
            adapter = CLAP(model_file, model=fake)
            v = adapter.extract(path)
            self.assertEqual(fake.seen, [((1, 480000), False)])
            self.assertEqual(v.shape, (2,))
            self.assertAlmostEqual(float(np.linalg.norm(v)), 1.0, places=5)

    def test_essentia_receives_16k_audio_and_mean_pools(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "reference.wav"
            model_file = Path(d) / "graph.pb"
            model_file.write_bytes(b"test-only")
            sine(path, seconds=3)
            fake = FakeEssentia()
            v = Essentia(model_file, model=fake).extract(path)
            self.assertEqual(fake.input_size, 48000)
            self.assertAlmostEqual(float(v[0]), 1.0)

    def test_missing_weights_not_silently_replaced_with_other_backends(self):
        b, _ = construct_backends(["dsp", "clap", "essentia"])
        self.assertIsInstance(b["dsp"], DSP)
        self.assertIsInstance(b["clap"], BackendUnavailable)
        self.assertIsInstance(b["essentia"], BackendUnavailable)


class Evaluation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / "audio"
        self.audio.mkdir()
        for name, freq in [("reference.wav", 220), ("near.wav", 220), ("far.wav", 900)]:
            sine(self.audio / name, freq=freq)
        self.file = self.root / "judgments.json"
        self.document = {"queries": [{"id": "one", "reference": "audio/reference.wav",
                                     "candidates": [{"id": "far", "path": "audio/far.wav", "relevance": 0},
                                                    {"id": "near", "path": "audio/near.wav", "relevance": 3}]}]}
        self.file.write_text(json.dumps(self.document), encoding="utf8")

    def test_dataset_and_metrics(self):
        rows = load_dataset(self.file)
        self.assertEqual(rows[0]["candidates"][1]["relevance"], 3)
        self.assertAlmostEqual(ndcg_at_k([3, 0], 2), 1.0)
        self.assertLess(ndcg_at_k([0, 3], 2), 1.0)
        self.assertEqual(precision_at_k([3, 0], 1), 1.0)
        self.assertEqual(precision_at_k([0, 3], 1), 0.0)

    def test_dataset_rejects_invalid_human_judgments(self):
        self.document["queries"][0]["candidates"][0]["relevance"] = "3"
        self.file.write_text(json.dumps(self.document))
        self.assertRaises(DatasetError, load_dataset, self.file)

    def test_dataset_rejects_reference_leakage(self):
        self.document["queries"][0]["candidates"][0]["path"] = "audio/reference.wav"
        self.file.write_text(json.dumps(self.document))
        self.assertRaises(DatasetError, load_dataset, self.file)

    def test_dataset_rejects_missing_files(self):
        self.document["queries"][0]["candidates"][0]["path"] = "audio/missing.wav"
        self.file.write_text(json.dumps(self.document))
        self.assertRaises(DatasetError, load_dataset, self.file)

    def test_embedding_ranking_and_cache(self):
        b = FakeEmbeddingBackend()
        ds = load_dataset(self.file)
        cache_dir = self.root / "cache"
        result = evaluate(ds, {"fake_embeddings": b}, cache_dir=cache_dir, k=1)
        record = result["methods"]["fake_embeddings"]
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["mean_precision_at_k"], 1.0)
        self.assertEqual(record["queries"][0]["rankings"][0]["id"], "near")
        self.assertEqual(b.calls, 3)
        again = evaluate(ds, {"fake_embeddings": b}, cache_dir=cache_dir, k=1)
        self.assertEqual(again["methods"]["fake_embeddings"]["cache_hits"], 3)
        self.assertEqual(b.calls, 3)

    def test_dsp_end_to_end_real_synthetic_audio(self):
        ds = load_dataset(self.file)
        result = evaluate(ds, {"dsp": DSP(seconds=3)}, k=1)
        record = result["methods"]["dsp"]
        self.assertEqual(record["status"], "complete", record["errors"])
        self.assertEqual(record["queries_evaluated"], 1)
        self.assertEqual(record["queries"][0]["rankings"][0]["id"], "near")

    def test_unavailable_backend_is_explicit(self):
        ds = load_dataset(self.file)
        result = evaluate(ds, {"clap": BackendUnavailable("no local model")})
        self.assertEqual(result["methods"]["clap"]["status"], "unavailable")
        self.assertNotIn("mean_ndcg_at_k", result["methods"]["clap"])

    def test_cache_key_changes_on_file_change(self):
        p = self.audio / "near.wav"
        b = DSP(seconds=3)
        a = backend_cache_key(p, b, None)
        sf.write(p, np.zeros(100), 22050)
        self.assertNotEqual(a, backend_cache_key(p, b, None))


if __name__ == "__main__":
    unittest.main()
