"""Reproducible offline Vibe Finder pipeline benchmark (no Jamendo API calls).

Creates short synthetic audio tracks in a temporary directory, simulates network
latency during each download and runs the real librosa-based analysis in worker
processes. The numbers do not predict real Jamendo/network performance.
"""
from __future__ import annotations

import argparse
from functools import partial
import shutil
import tempfile
import time
import sys
from pathlib import Path

# Allow `python scripts/bench_pipeline.py` to resolve project modules.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import soundfile as sf

from candidate_pipeline import scan_candidates
from engine import GROUPS, analyse


def simulate_download(track: dict, dest: Path, *, delay: float = 0.08) -> Path:
    time.sleep(delay)
    shutil.copyfile(track["local_source"], dest)
    return dest


def reference_audio(directory: Path, count: int, seconds: int = 4) -> tuple[Path, list[dict]]:
    tracks = []
    sr = 22050
    secs = seconds
    t = np.arange(sr * secs, dtype=np.float32) / sr
    for n in range(count + 1):
        # Different harmonics/beat envelopes; entirely generated, no copyrighted audio.
        base = 55 + 4 * n
        kicks = np.exp(-14 * (t % (0.42 + 0.01*n)))
        y = (0.32 * np.sin(2*np.pi*base*t) + 0.25*np.sin(2*np.pi*(220+9*n)*t)
             + 0.10*np.sin(2*np.pi*(440+5*n)*t)) * (0.42 + 0.58*kicks)
        path = directory / f"synthetic_{n:02d}.wav"
        sf.write(path, y, sr)
        if n:
            tracks.append({"id":f"song-{n}", "artist":"Generated", "title":f"Synthetic {n}",
                           "audio_url":f"https://fake.invalid/{n}", "local_source":str(path)})
    return directory / "synthetic_00.wav", tracks


def benchmark(n: int, configs: list[tuple[int, int]], *, seconds: int = 4, delay: float = 0.08) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        ref_path, tracks = reference_audio(root, n, seconds=seconds)
        ref = analyse(ref_path, max_seconds=seconds)
        weights = {g:1 for g in GROUPS}
        print(f"Synthetic WAV tracks: {n}; track duration: {seconds} seconds; download delay: {delay*1000:.0f}ms")
        for down, cpu in configs:
            cache = root / f"cache_{down}_{cpu}.json"
            start = time.perf_counter()
            first = None
            events = scan_candidates(tracks, ref, weights, cache,
                                     download_workers=down, analysis_workers=cpu,
                                     downloader=partial(simulate_download, delay=delay))
            received = 0
            for event in events:
                received += bool(event['match'])
                if first is None:
                    first = time.perf_counter() - start
            elapsed = time.perf_counter() - start
            print(f"{down} downloads / {cpu} processes: first={first:.2f}s, "
                  f"total={elapsed:.2f}s, matches={received}/{n}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks",type=int,default=8)
    parser.add_argument("--seconds", type=int, default=4, help="synthetic track length (2–50 s)")
    parser.add_argument("--latency-ms", type=int, default=80, help="simulated per-track download latency")
    args = parser.parse_args()
    if not 2 <= args.seconds <= 50 or not 0 <= args.latency_ms <= 30000 or not 1 <= args.tracks <= 100:
        parser.error("Choose 1–100 tracks, 2–50 seconds and 0–30000ms latency")
    benchmark(args.tracks, [(1,1), (4,2)], seconds=args.seconds, delay=args.latency_ms / 1000)
