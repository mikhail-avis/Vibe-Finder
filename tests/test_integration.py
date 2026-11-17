"""Real DSP integration through simulated Jamendo HTTP responses; no network needed."""
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import numpy as np
import soundfile as sf

from candidate_pipeline import scan_candidates
from engine import GROUPS, analyse
from jamendo import find_candidates, temporary_audio


class FakeHTTPResponse:
    status_code = 200
    headers = {}

    def __init__(self, url, body=None, rows=None):
        self.url, self.body, self.rows = url, body, rows

    def raise_for_status(self):
        return None

    def json(self):
        return {"headers": {"status": "success"}, "results": self.rows}

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeJamendoHTTP:
    def __init__(self, wav):
        self.wav = wav
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == "https://api.jamendo.com/v3.0/tracks/":
            return FakeHTTPResponse(url, rows=[
                {"id": "123", "name": "Synthetic Test Track", "artist_name": "Generated",
                 "audiodownload_allowed": True,
                 "audiodownload": "https://prod-1.storage.jamendo.com/download/track/123/mp32/",
                 "shareurl": "https://www.jamendo.com/track/123"},
                {"id": "456", "audiodownload_allowed": False,
                 "audiodownload": "https://prod-1.storage.jamendo.com/download/track/456/mp32/"},
            ])
        if url == "https://prod-1.storage.jamendo.com/download/track/123/mp32/":
            return FakeHTTPResponse(url, body=self.wav)
        raise AssertionError(f"Unexpected HTTP request to {url}")


class EndToEndTests(unittest.TestCase):
    def test_jamendo_discovery_through_actual_waveform_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sr = 22050
            t = np.arange(sr * 4, dtype=np.float32) / sr
            audio = (.15 * np.sin(2 * np.pi * 90 * t) +
                     .1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            wav = io.BytesIO()
            sf.write(wav, audio, sr, format="WAV")
            (root / 'reference.wav').write_bytes(wav.getvalue())
            reference = analyse(root / 'reference.wav', max_seconds=4)
            http = FakeJamendoHTTP(wav.getvalue())
            candidates, warnings = find_candidates('abcd1234', max_tracks=4,
                                                   tags=('electronic',), session=http)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(warnings, [])
            events = list(scan_candidates(candidates, reference,
                           {g: 1 for g in GROUPS}, root / 'fingerprints.json',
                           downloader=partial(temporary_audio, session=http),
                           analysis_executor_cls=ThreadPoolExecutor))
            self.assertEqual(len(events), 1)
            match = events[0]['match']
            self.assertIsNone(events[0]['error'])
            self.assertEqual(match['id'], '123')
            self.assertGreater(match['score'], 90)
            self.assertEqual(len(http.calls), 2)
            self.assertFalse(list(root.rglob('candidate_*.mp3')))
            cache = (root / 'fingerprints.json').read_text()
            self.assertNotIn('abcd1234', cache)
            self.assertNotIn('RIFF', cache)
            # A second search must not re-download or re-analyse the track.
            again = list(scan_candidates(candidates, reference,
                           {g: 1 for g in GROUPS}, root / 'fingerprints.json',
                           downloader=partial(temporary_audio, session=http),
                           analysis_executor_cls=ThreadPoolExecutor))
            self.assertTrue(again[0]['match']['cached'])
            self.assertEqual(len(http.calls), 2)


if __name__ == '__main__':
    unittest.main()
