"""Deterministic pipeline tests: no live API, downloads or process spawning."""
import tempfile
import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from candidate_pipeline import scan_candidates, _read_cache
from engine import GROUPS


def descriptors(path: Path):
    """Cheap deterministic fake DSP analysis."""
    value = 0.15 + (int(path.stem.split('_')[-1]) * 0.08)
    return {"bpm": 100.0, **{name: [value, value / 2] for name in GROUPS}}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.cache = Path(self.td.name) / 'user-data' / 'features.json'
        self.reference = {"bpm": 100.0, **{name: [0.15, 0.075] for name in GROUPS}}
        self.weights = {name: 1 for name in GROUPS}
        self.tracks = [{"id": str(n), "artist": "Example", "title": f"Song {n}",
                        "audio_url": f"https://mp3d.jamendo.com/example/{n}"} for n in range(8)]

    def test_parallel_score_cache_and_failure_isolation(self):
        attempts = []
        def fake_download(track, dest):
            attempts.append(track['id'])
            if track['id'] == '6':
                raise OSError('simulated request failure')
            time.sleep(0.01)
            dest.write_bytes(b'fake audio')
            return dest
        args = dict(download_workers=4, analysis_workers=2,
                    downloader=fake_download, analyser=descriptors,
                    analysis_executor_cls=ThreadPoolExecutor)
        events = list(scan_candidates(self.tracks, self.reference, self.weights, self.cache, **args))
        self.assertEqual(len(events), 8)
        self.assertEqual(events[-1]['completed'], 8)
        results = [e['match'] for e in events if e['match']]
        self.assertEqual(len(results), 7)
        self.assertEqual(len([e for e in events if e['error']]), 1)
        self.assertEqual(set(attempts), set(str(n) for n in range(8)))
        self.assertEqual(len(_read_cache(self.cache)), 7)
        self.assertFalse(any(Path(self.td.name).glob('candidate_*.mp3')))
        attempts.clear()
        second = list(scan_candidates(self.tracks, self.reference, self.weights, self.cache, **args))
        self.assertEqual(len(second), 8)
        self.assertEqual(attempts, ['6'])  # one earlier failed track is retried
        self.assertTrue(all(e['match']['cached'] for e in second if e['match']))

    def test_download_backpressure_bounds_temporary_files(self):
        """Fast downloads must not build an unbounded pile ahead of DSP."""
        peak_files = 0
        lock = threading.Lock()

        def quick_download(track, dest):
            nonlocal peak_files
            dest.write_bytes(b"temporary audio")
            with lock:
                peak_files = max(peak_files, len(list(dest.parent.glob("candidate_*.mp3"))))
            return dest

        def slow_analysis(path):
            time.sleep(0.035)
            return descriptors(path)

        extended = self.tracks + [
            {"id": str(n), "artist": "Example", "title": f"Song {n}",
             "audio_url": f"https://mp3d.jamendo.com/example/{n}"}
            for n in range(8, 24)
        ]
        events = list(scan_candidates(
            extended, self.reference, self.weights, self.cache,
            download_workers=4, analysis_workers=2,
            downloader=quick_download, analyser=slow_analysis,
            analysis_executor_cls=ThreadPoolExecutor,
        ))
        self.assertEqual(len(events), len(extended))
        self.assertEqual(sum(bool(event["match"]) for event in events), len(extended))
        self.assertLessEqual(peak_files, 6)

    def test_zero_tracks_and_invalid_settings(self):
        self.assertEqual(list(scan_candidates([], self.reference, self.weights, self.cache)), [])
        with self.assertRaises(ValueError):
            list(scan_candidates(self.tracks, self.reference, self.weights, self.cache,
                                 download_workers=0))

    def test_cache_has_no_raw_audio_or_client_id(self):
        def fake_download(track, dest):
            dest.write_bytes(b'secret raw audio')
            return dest
        list(scan_candidates(self.tracks[:1], self.reference, self.weights, self.cache,
                             downloader=fake_download, analyser=descriptors,
                             analysis_executor_cls=ThreadPoolExecutor))
        text = self.cache.read_text()
        self.assertNotIn('secret raw audio', text)
        self.assertNotIn('abcd1234', text)

if __name__ == '__main__':
    unittest.main()
