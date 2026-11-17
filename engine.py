"""Audio-based similarity engine for an offline, local track catalogue.

This is an interpretable DSP baseline, NOT a trained semantic audio embedding
model. Scores are heuristic match indices, not probabilities or validated ratings.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import librosa
import numpy as np

SR = 22050
HOP = 512
MAX_SECONDS = 120
SUPPORTED_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".m4a"}
GROUPS = ("Rhythm", "Bass", "Timbre", "Atmosphere", "Energy")


def _sanitize(values: Any) -> list[float]:
    array = np.asarray(values, dtype=np.float64).ravel()
    return np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0).round(7).tolist()


def analyse(path: str | Path, max_seconds: int = MAX_SECONDS) -> dict[str, Any]:
    """Create five groups of descriptors from the first max_seconds of audio."""
    path = Path(path)
    y, sr = librosa.load(str(path), sr=SR, mono=True, duration=max_seconds)
    if len(y) < SR * 2:
        raise ValueError("Audio must be at least two seconds long.")
    # Peak-normalise for level-independent comparison.
    peak = np.max(np.abs(y))
    if peak < 1e-7:
        raise ValueError("Audio is silent.")
    y = (y / peak).astype(np.float32)
    duration = len(y) / sr

    stft = librosa.stft(y, n_fft=2048, hop_length=HOP)
    power = np.abs(stft) ** 2
    frequencies = librosa.fft_frequencies(sr=sr, n_fft=2048)
    onset = librosa.onset.onset_strength(S=librosa.power_to_db(power + 1e-8), sr=sr, hop_length=HOP)
    onsets = librosa.onset.onset_detect(onset_envelope=onset, sr=sr, hop_length=HOP)
    try:
        raw_tempo = float(librosa.feature.tempo(onset_envelope=onset, sr=sr, hop_length=HOP, aggregate=np.median)[0])
    except (ValueError, IndexError):
        raw_tempo = 0.0

    # Modulation frequency spectrum of attack strengths, normalised so
    # matching does not rely solely on a sometimes-ambiguous BPM estimate.
    env = onset - float(np.mean(onset))
    modulation = np.abs(np.fft.rfft(env))
    mod_freq = np.fft.rfftfreq(len(env), d=HOP / sr)
    modulation_bins = np.interp(np.linspace(0.5, 12, 24), mod_freq, modulation)
    modulation_bins /= (np.linalg.norm(modulation_bins) + 1e-8)

    # Normalised coarse frequency bands: approximates sub-bass / bass texture.
    edges = [20, 55, 110, 220, 450, 900, 2200, 6000, 11025]
    band_energy = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (frequencies >= lo) & (frequencies < hi)
        band_energy.append(float(np.mean(np.sum(power[mask], axis=0))) if mask.any() else 0.0)
    band_energy = np.asarray(band_energy)
    bands = np.sqrt(band_energy / (np.sum(band_energy) + 1e-8))

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=HOP)
    contrast = librosa.feature.spectral_contrast(S=np.sqrt(power) + 1e-8, sr=sr)
    chroma = librosa.feature.chroma_stft(S=power, sr=sr, hop_length=HOP)
    rms = librosa.feature.rms(S=np.abs(stft))[0]
    centroid = librosa.feature.spectral_centroid(S=np.sqrt(power), sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(S=power + 1e-8)[0]
    zcr = librosa.feature.zero_crossing_rate(y, hop_length=HOP)[0]

    # Normalize and squash descriptors into reasonably comparable ranges.
    # These remain hand-engineered heuristics that will be calibrated later.
    rhythms = np.r_[modulation_bins, np.log1p(len(onsets) / duration) / 3.0]
    timbre = np.r_[np.mean(mfcc, axis=1) / 100.0, np.std(mfcc, axis=1) / 100.0,
                   np.mean(centroid) / sr, np.mean(flatness), np.mean(zcr)]
    atmosphere = np.r_[np.mean(contrast, axis=1) / 50.0,
                       np.mean(chroma, axis=1), np.std(chroma, axis=1)]
    energy = np.array([np.mean(rms), np.std(rms), np.quantile(rms, .85) - np.quantile(rms, .15),
                       np.mean(onset) / 10.0, np.std(onset) / 10.0])

    return {
        "duration": round(duration, 2),
        "bpm": round(raw_tempo, 1),
        "Rhythm": _sanitize(rhythms),
        "Bass": _sanitize(bands),
        "Timbre": _sanitize(timbre),
        "Atmosphere": _sanitize(atmosphere),
        "Energy": _sanitize(energy),
    }


def _similarity(a: list[float], b: list[float]) -> float:
    x, y = np.asarray(a), np.asarray(b)
    if x.shape != y.shape:
        raise ValueError("Incompatible feature vectors.")
    # Bounded symmetric relative distance; avoids any dependency on the size
    # or composition of the candidate catalogue.
    distance = np.mean(np.abs(x-y) / (np.maximum(np.abs(x), np.abs(y)) + 0.15))
    return float(np.clip(np.exp(-2.0 * distance), 0, 1))


def _tempo_similarity(first: float, second: float) -> float:
    if first <= 0 or second <= 0:
        return 0.5  # indeterminate BPM; rhythm patterns still contribute
    delta = abs(np.log2(first / second))
    # Allow for half/double-time beats but penalise these relative to same BPM.
    direct = np.exp(-0.5 * (delta / 0.18)**2)
    half_or_double = 0.76 * np.exp(-0.5 * (abs(delta - 1) / 0.18)**2)
    return float(max(direct, half_or_double))


def compare(reference: dict, candidate: dict, weights: dict[str, int] | None = None) -> dict:
    weights = weights or {g: 1 for g in GROUPS}
    parts = {g: _similarity(reference[g], candidate[g]) for g in GROUPS}
    parts["Rhythm"] = 0.65*parts["Rhythm"] + 0.35*_tempo_similarity(reference["bpm"], candidate["bpm"])
    denom = sum(max(0, weights.get(g, 0)) for g in GROUPS)
    if denom <= 0:
        raise ValueError("At least one similarity weight must be positive.")
    aggregate = sum(parts[g] * max(0, weights.get(g, 0)) for g in GROUPS) / denom
    best = sorted(GROUPS, key=lambda group: parts[group], reverse=True)
    return {"score": round(aggregate * 100, 1),
            "components": {g: round(parts[g]*100, 1) for g in GROUPS},
            "strengths": best[:2]}


def audio_files(directory: str | Path) -> list[Path]:
    base = Path(directory).expanduser()
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)


def library_features(directory: str | Path, progress=None) -> tuple[list[dict], list[str]]:
    """Maintain a local JSON feature cache; never stores raw audio."""
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        return [], [f"Folder does not exist: {directory}"]
    paths = audio_files(directory)
    cache_dir = Path(__file__).resolve().parent / ".cache"
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / "library.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        cache = {}
    indexed = []
    errors = []
    for idx, path in enumerate(paths):
        stat = path.stat()
        # Include canonical path and modification timestamp to prevent stale matches.
        cache_key = hashlib.sha256(f"{path}|{stat.st_size}|{stat.st_mtime_ns}|v2".encode()).hexdigest()
        if cache_key not in cache:
            try:
                cache[cache_key] = analyse(path)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
                if progress:
                    progress(idx+1, len(paths))
                continue
        indexed.append({"path": path, "features": cache[cache_key]})
        if progress:
            progress(idx+1, len(paths))
    try:
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    except OSError as exc:
        errors.append(f"Could not save cache: {exc}")
    return indexed, errors
