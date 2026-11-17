"""One-reference, many-candidate acoustic discovery for the experimental audio lab.

Only licence-eligible, downloadable catalogue recordings are eligible. Their
*decoded audio* is scored; artist names, titles and genre labels never enter
similarity calculation. Audio downloads are temporary and not retained.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from catalogues import SOURCES, discover_across_sources
from downloaders import download_track_audio
from jamendo import DEFAULT_CLIENT_ID

MAX_CANDIDATES = 24
AUDIO_SUFFIXES = {'.mp3', '.wav', '.ogg', '.oga', '.flac', '.m4a', '.opus'}


def find_similar_audio(
    reference_audio: Path, backend, *, sources: tuple[str, ...] = SOURCES,
    sample_count: int = 12, client_id: str = DEFAULT_CLIENT_ID,
    finder=discover_across_sources, downloader=download_track_audio,
    on_progress=None,
) -> dict:
    """Rank downloaded recordings against a single real-audio reference.

    A controlled, bounded number of audio downloads run concurrently, while
    inference is serialized to avoid unsafe concurrent model usage on Windows.
    The injected finder/downloader allow offline verification with WAV fixtures.
    """
    if not isinstance(sample_count, int) or not 1 <= sample_count <= MAX_CANDIDATES:
        raise ValueError(f'Choose between 1 and {MAX_CANDIDATES} candidates')
    sources = tuple(dict.fromkeys(sources))
    if not sources or any(name not in SOURCES for name in sources):
        raise ValueError('Select at least one supported music catalogue')
    reference_audio = Path(reference_audio)
    if not reference_audio.is_file():
        raise ValueError('Record or upload your reference song first')
    # Fail before reaching network if model/checkpoint/audio decoding is unusable.
    reference_features = backend.extract(reference_audio)
    candidates, warnings = finder(client_id, sample_count, sources=sources)
    if not isinstance(candidates, list):
        raise ValueError('Catalogue returned an invalid candidate list')
    candidates = candidates[:sample_count]
    failures, rankings = [], []
    processed = 0
    with tempfile.TemporaryDirectory(prefix='vibe-experimental-search-') as directory:
        root = Path(directory)
        # Small batches limit unprocessed temporary audio on disk to 3 files.
        for offset in range(0, len(candidates), 3):
            batch = candidates[offset:offset + 3]
            with ThreadPoolExecutor(max_workers=min(3, len(batch))) as pool:
                futures = {}
                for i, track in enumerate(batch):
                    url = str(track.get('audio_url') or '')
                    ext = Path(urlsplit(url).path).suffix.lower()
                    if ext not in AUDIO_SUFFIXES:
                        ext = '.mp3'
                    target = root / f'candidate_{offset+i}{ext}'
                    futures[pool.submit(downloader, track, target)] = (track, target)
                # Only the main thread runs the model's feature extractor.
                for future in as_completed(futures):
                    track, target = futures[future]
                    try:
                        path = Path(future.result())
                        if not path.is_file():
                            raise ValueError('Audio file was not downloaded')
                        features = backend.extract(path)
                        score = float(backend.similarity(reference_features, features))
                        if not math.isfinite(score):
                            raise ValueError('Audio model returned a non-finite score')
                        rankings.append({
                            'source': str(track.get('source') or 'Unknown'),
                            'id': str(track.get('id') or ''),
                            'artist': str(track.get('artist') or 'Unknown artist'),
                            'title': str(track.get('title') or 'Untitled'),
                            'listen_url': str(track.get('listen_url') or ''),
                            'license_url': str(track.get('license_url') or ''),
                            'score': score,
                        })
                    except Exception as exc:
                        failures.append(f"{track.get('source', 'Catalogue')}: {track.get('title', 'Track')}: {exc}")
                    finally:
                        target.unlink(missing_ok=True)
                        processed += 1
                        if on_progress:
                            on_progress(processed, len(candidates), len(rankings))
    rankings.sort(key=lambda item: item['score'], reverse=True)
    return {
        'ranked': rankings, 'sampled': len(candidates),
        'analysed': len(rankings), 'skipped': failures,
        'warnings': list(warnings or []), 'backend': str(backend.name),
        'sources': sources,
    }
