import unittest
from discovery import youtube_id, style_terms, get_search_terms, discover_tracks


class FakeResponse:
    def raise_for_status(self):
        return None
    def json(self):
        return {"results": [
            {"kind": "song", "trackId": 123, "trackName": "Night Drive", "artistName": "Other Artist", "primaryGenreName": "Phonk", "trackViewUrl": "https://music.apple.com/example"},
            {"kind": "song", "trackId": 456, "trackName": "ECLIPSE", "artistName": "HXVRMXN", "primaryGenreName": "Phonk", "trackViewUrl": "https://music.apple.com/other"},
        ]}


class DiscoveryTests(unittest.TestCase):
    def test_youtube_urls(self):
        self.assertEqual(youtube_id("https://youtu.be/JD0Ve7psY_I"), "JD0Ve7psY_I")
        self.assertEqual(youtube_id("https://music.youtube.com/watch?v=JD0Ve7psY_I"), "JD0Ve7psY_I")
        self.assertIsNone(youtube_id("https://evil-youtube.com/watch?v=JD0Ve7psY_I"))
    def test_genre_cues(self):
        self.assertIn("phonk", style_terms("HXVRMXN - ECLIPSE (Night Drive Phonk)"))
        self.assertIn("phonk", " ".join(get_search_terms("HXVRMXN - ECLIPSE (Night Drive Phonk)")))
    def test_metadata_mode_is_not_audio_scoring(self):
        def mock_get(url, **kwargs):
            self.assertEqual(url, "https://itunes.apple.com/search")
            return FakeResponse()
        tracks, problems = discover_tracks("HXVRMXN - ECLIPSE (Night Drive Phonk)", get=mock_get)
        self.assertFalse(problems)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["artist"], "Other Artist")
        self.assertNotIn("score", tracks[0])
        self.assertIn("youtube.com/results?search_query=", tracks[0]["youtube_search_url"])


if __name__ == "__main__":
    unittest.main()
