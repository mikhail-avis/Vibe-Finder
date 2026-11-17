import json
import tempfile
import unittest
from pathlib import Path
from history import read_history, save_search


class HistoryTests(unittest.TestCase):
    def test_metadata_history_no_audio_and_limited_items(self):
        with tempfile.TemporaryDirectory() as t:
            file = Path(t) / 'user-data' / 'search-history.json'
            self.assertEqual(read_history(file), [])
            for i in range(35):
                save_search(file, reference_label=f'song-{i}',
                            examined=4, adaptive=True, weights={'Bass': 5},
                            matches=[{'id': i, 'artist': 'Example', 'title': 'Test', 'score': 60.1,
                                      'audio_url': 'https://music-secret.invalid/file',
                                      'client_id': 'SECRET-KEY', 'features': 'raw-audio'}])
            history = read_history(file)
            self.assertEqual(len(history), 30)
            self.assertEqual(history[0]['reference'], 'song-34')
            self.assertNotIn('client_id', file.read_text())
            self.assertNotIn('SECRET-KEY', file.read_text())
            self.assertNotIn('raw-audio', file.read_text())
            self.assertNotIn('music-secret.invalid', file.read_text())

    def test_corrupt_history_is_ignored(self):
        with tempfile.TemporaryDirectory() as t:
            file=Path(t)/'search.json'
            file.write_text('{broken')
            self.assertEqual(read_history(file), [])
            file.write_text('"not a list"')
            self.assertEqual(read_history(file), [])

if __name__ == '__main__': unittest.main()
