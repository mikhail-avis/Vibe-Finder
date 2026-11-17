"""Regression coverage: browser-pasted public music links must be convenient but safe."""
from __future__ import annotations

import io
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from experiments.link_inputs import capture_link_audio, capture_is_current, inspect_music_link
from experimental_landing import store_input
from experiments.audio_models import DSP


class LinkNormalizationTests(unittest.TestCase):
    def test_screenshot_youtube_url_is_accepted_with_https_added(self):
        actual = inspect_music_link('www.youtube.com/watch?v=zuypfiE2Faw&list=LL')
        self.assertEqual(actual['url'], 'https://www.youtube.com/watch?v=zuypfiE2Faw&list=LL')
        self.assertEqual(actual['provider'], 'YouTube')
        self.assertTrue(actual['added_https'])

    def test_common_public_music_links_without_scheme(self):
        cases = {
            'youtube.com/watch?v=abc123': ('YouTube', 'https://youtube.com/watch?v=abc123'),
            'youtu.be/abc123': ('YouTube', 'https://youtu.be/abc123'),
            'music.youtube.com/watch?v=abc123': ('YouTube', 'https://music.youtube.com/watch?v=abc123'),
            'soundcloud.com/creator/track': ('SoundCloud', 'https://soundcloud.com/creator/track'),
            'open.spotify.com/track/abc': ('Spotify', 'https://open.spotify.com/track/abc'),
            'artist.bandcamp.com/track/song': ('Bandcamp', 'https://artist.bandcamp.com/track/song'),
            'www.jamendo.com/track/abc': ('Jamendo', 'https://www.jamendo.com/track/abc'),
            'commons.wikimedia.org/wiki/File:Example.ogg': ('Wikimedia', 'https://commons.wikimedia.org/wiki/File:Example.ogg'),
            'example.org/song?q=1&x=two': ('Music website', 'https://example.org/song?q=1&x=two'),
        }
        for value, (provider, url) in cases.items():
            with self.subTest(value=value):
                info = inspect_music_link(value)
                self.assertEqual(info['provider'], provider)
                self.assertEqual(info['url'], url)
                self.assertTrue(info['added_https'])

    def test_already_https_is_not_marked_as_modified(self):
        value = 'https://www.youtube.com/watch?v=zuypfiE2Faw&list=LL'
        actual = inspect_music_link(value)
        self.assertEqual(actual['url'], value)
        self.assertFalse(actual['added_https'])

    def test_trailing_leading_spaces_are_trimmed(self):
        actual = inspect_music_link('  www.youtube.com/watch?v=abc  ')
        self.assertEqual(actual['url'], 'https://www.youtube.com/watch?v=abc')

    def test_unsafe_addresses_and_schemes_are_rejected(self):
        blocked = [
            '', 'not a link', '//youtube.com/watch?v=x', 'http://youtube.com/watch?v=x',
            'file:///C:/Users/secret.txt', 'javascript:alert(1)', 'data:text/html,hi',
            'mailto:a@example.com', 'ftp://example.org/song',
            'localhost:8501', 'localhost/song', 'https://localhost/song',
            '127.0.0.1:8501/song', '127.0.0.1/song', '192.168.0.1/song',
            'https://[::1]/song', 'music.local/song', 'server.internal/song',
            'username:pass@youtube.com/watch?v=x', 'https://user:pw@youtube.com/watch?v=x',
            'youtube.com:8080/watch?v=x', 'https://youtube.com:8080/watch?v=x',
            'evil.com\\@youtube.com/watch?v=x',
            'https://example.org/some\nthing', 'www.youtube.com/watch?v=x y',
            'www.youtube.com\\evil.com/watch?v=x',
            'https:///not-a-real-host',
        ]
        for value in blocked:
            with self.subTest(value=value), self.assertRaises(ValueError):
                inspect_music_link(value)

    def test_provider_spoofing_is_not_labeled_youtube(self):
        actual = inspect_music_link('youtube.com.evil.example/watch?v=a')
        self.assertEqual(actual['provider'], 'Music website')

    def test_current_capture_accepts_equivalent_bare_and_https_link(self):
        song = 'www.youtube.com/watch?v=zuypfiE2Faw&list=LL'
        data = _test_wav()
        recorder = Mock(return_value=data)
        result = capture_link_audio(song, 7, 10, recorder=recorder)
        recorder.assert_called_once_with(10, device_index=7)
        self.assertEqual(result['url'], 'https://' + song)
        self.assertTrue(capture_is_current(result, song))
        self.assertTrue(capture_is_current(result, 'https://' + song))
        self.assertFalse(capture_is_current(result, 'youtu.be/not-the-same'))

    def test_link_cannot_be_passed_as_fake_audio(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'URL alone is not audio'):
                store_input('Music link', None, None, Path(tmp), 'reference')

    def test_recorded_waveform_can_be_decoded_and_analysed(self):
        data = _test_wav()
        captured = capture_link_audio('youtu.be/real-song', 2, 10, recorder=lambda *_a, **_kw: data)
        with TemporaryDirectory() as tmp:
            audio = store_input('Music link', None, captured, Path(tmp), 'reference')
            self.assertEqual(audio.read_bytes(), data)
            features = DSP(seconds=2).extract(audio)
            self.assertIn('bpm', features)


def _test_wav() -> bytes:
    import numpy as np
    import soundfile as sf
    srate = 16000
    t = np.arange(srate * 2) / srate
    out = io.BytesIO()
    sf.write(out, (0.2 * np.sin(2 * np.pi * 220 * t)).astype('float32'), srate, format='WAV')
    return out.getvalue()


if __name__ == '__main__':
    unittest.main()
