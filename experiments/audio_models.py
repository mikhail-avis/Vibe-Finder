"""Optional *real audio* similarity backends for a controlled Vibe Finder benchmark.

No model is downloaded automatically. No inference results are calibrated
probabilities. Native Windows Essentia/TensorFlow support is not assumed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from engine import GROUPS, analyse, compare

MAX_SECONDS = 50


class BackendUnavailable(RuntimeError):
    """Missing, untrusted or unsupported local model configuration."""


class Backend(Protocol):
    name: str
    def extract(self, path: Path): ...
    def similarity(self, reference, candidate) -> float: ...


def cosine_similarity(reference, candidate) -> float:
    """Cosine similarity ([-1, 1]); never a calibrated percentage."""
    a = np.asarray(reference, dtype=np.float64).ravel()
    b = np.asarray(candidate, dtype=np.float64).ravel()
    if not a.size or a.shape != b.shape or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        raise ValueError("Embeddings must be nonempty, finite vectors of equal length")
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator < 1e-12:
        raise ValueError("Empty-energy embedding cannot be compared")
    return float(np.clip(np.dot(a, b) / denominator, -1.0, 1.0))


def normalized_pool(embeddings) -> np.ndarray:
    """Pool adjacent audio patches into one unit vector; fail on invalid model output."""
    matrix = np.asarray(embeddings, dtype=np.float64)
    if matrix.ndim == 1:
        matrix = matrix[np.newaxis, :]
    if matrix.ndim != 2 or not matrix.size or not np.all(np.isfinite(matrix)):
        raise ValueError("Model returned invalid audio embeddings")
    norms = np.linalg.norm(matrix, axis=1)
    valid = matrix[norms > 1e-12] / norms[norms > 1e-12, np.newaxis]
    if not len(valid):
        raise ValueError("Model returned zero embeddings")
    pooled = valid.mean(axis=0)
    magnitude = float(np.linalg.norm(pooled))
    if magnitude < 1e-12:
        raise ValueError("Pooled embedding cancelled to zero")
    return (pooled / magnitude).astype(np.float32)


class DSP:
    name = "dsp"

    def __init__(self, seconds: int = MAX_SECONDS):
        self.seconds = seconds

    def extract(self, path: Path) -> dict:
        return analyse(path, max_seconds=self.seconds)

    def similarity(self, reference: dict, candidate: dict) -> float:
        # Reuses the exact same scoring as Vibe Finder v0.6.0, in [0,100].
        return float(compare(reference, candidate, {key: 1 for key in GROUPS})["score"])


class CLAP:
    name = "clap"

    def __init__(self, checkpoint: Path, seconds: int = MAX_SECONDS, model=None):
        self.checkpoint = Path(checkpoint)
        self.seconds = seconds
        if not self.checkpoint.is_file():
            raise BackendUnavailable("CLAP needs a local checkpoint file; no weights are downloaded automatically")
        if model is None:
            try:
                import laion_clap
            except ImportError as exc:
                raise BackendUnavailable("CLAP is not installed; use a separate optional Python environment") from exc
            model = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-base")
            # Only load a checkpoint from a source the user has verified: torch
            # checkpoints may contain serialized code and must not be untrusted.
            model.load_ckpt(str(self.checkpoint))
        self.model = model

    def extract(self, path: Path) -> np.ndarray:
        import librosa
        audio, _ = librosa.load(str(path), sr=48000, mono=True, duration=self.seconds)
        if audio.size < 2 * 48000 or float(np.max(np.abs(audio))) < 1e-7:
            raise ValueError("Audio is shorter than 2 seconds or silent")
        chunk = 480000  # CLAP documentation: 48 kHz and 10 seconds
        starts = np.linspace(0, max(0, len(audio) - chunk), num=3, dtype=int)
        windows = []
        for pos in dict.fromkeys(map(int, starts)):
            window = np.asarray(audio[pos:pos + chunk], dtype=np.float32)
            windows.append(np.pad(window, (0, max(0, chunk - len(window)))))
        values = self.model.get_audio_embedding_from_data(x=np.stack(windows), use_tensor=False)
        return normalized_pool(values)

    similarity = staticmethod(cosine_similarity)


class Essentia:
    name = "essentia"

    def __init__(self, graph: Path, seconds: int = MAX_SECONDS, model=None):
        self.graph = Path(graph)
        self.seconds = seconds
        if not self.graph.is_file():
            raise BackendUnavailable("Essentia needs a local .pb model; no weights are downloaded automatically")
        if model is None:
            try:
                from essentia.standard import TensorflowPredictEffnetDiscogs
            except ImportError as exc:
                raise BackendUnavailable("Essentia-TensorFlow unavailable: use a compatible Linux/macOS or WSL environment") from exc
            model = TensorflowPredictEffnetDiscogs(graphFilename=str(self.graph), output="PartitionedCall:1")
        self.model = model

    def extract(self, path: Path) -> np.ndarray:
        import librosa
        audio, _ = librosa.load(str(path), sr=16000, mono=True, duration=self.seconds)
        if audio.size < 2 * 16000 or float(np.max(np.abs(audio))) < 1e-7:
            raise ValueError("Audio is shorter than 2 seconds or silent")
        patches = self.model(np.asarray(audio, dtype=np.float32))
        return normalized_pool(patches)

    similarity = staticmethod(cosine_similarity)