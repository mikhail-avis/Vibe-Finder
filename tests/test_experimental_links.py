"""Links require user-initiated playback capture; never metadata-only matching."""
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from experiments.link_inputs import capture_link_audio, capture_is_current, inspect_music_link
from experimental_landing import store_input
from experiments.audio_models import DSP


def wav_fixture():
    import numpy as np
    import soundfile as sf
    sample_rate = 16000
    data = .15 * np.sin(2*np.pi*220*np.arange(sample_rate*2)/sample_rate).astype('float32')
    raw = io.BytesIO()
    sf.write(raw, data, sample_rate, format='WAV')
    return raw.getvalue()


class LinkInputTests(unittest.TestCase):
    def test_recognises_music_providers(self):
        for link, provider in [
            ('https://www.youtube.com/watch?v=abc123', 'YouTube'),
            ('https://music.youtube.com/watch?v=abc123', 'YouTube'),
            ('https://youtu.be/abc123', 'YouTube'),
            ('https://soundcloud.com/creator/track', 'SoundCloud'),
            ('https://open.spotify.com/track/abcdef', 'Spotify'),
            ('https://artist.bandcamp.com/track/a', 'Bandcamp'),
            ('https://www.jamendo.com/track/77', 'Jamendo'),
            ('https://commons.wikimedia.org/wiki/File:Example.ogg', 'Wikimedia'),
            ('https://example.org/music?id=1', 'Music website'),
        ]:
            with self.subTest(provider=provider,link=link):
                self.assertEqual(inspect_music_link(link)['provider'], provider)

    def test_rejects_unsafe_and_non_web_links(self):
        for link in ['file:///c:/track.mp3', 'javascript:alert(1)',
            'http://www.youtube.com/watch?v=abc', 'https://localhost/x',
            'https://192.168.0.1/track', 'https://127.0.0.1/',
            'https://user:pass@youtube.com/watch?v=1',
            'https://www.youtube.com:8080/watch?v=1', 'https://music.local/track',
            'not a link', '']:
            with self.subTest(link=link), self.assertRaises(ValueError):
                inspect_music_link(link)

    def test_invalid_link_never_calls_recorder(self):
        def bad_capture(*args,**kwargs):
            raise AssertionError('Must not capture')
        with self.assertRaises(ValueError):
            capture_link_audio('https://localhost/evil',3,20,recorder=bad_capture)

    def test_capture_uses_explicit_output_without_downloading_music(self):
        requests=[]
        data=wav_fixture()
        def recorder(seconds,*,device_index):
            requests.append((seconds,device_index))
            return data
        recording=capture_link_audio('https://music.youtube.com/watch?v=example',9,20,recorder=recorder)
        self.assertEqual(requests,[(20,9)])
        self.assertEqual(recording['wav'],data)
        self.assertEqual(recording['provider'],'YouTube')
        self.assertTrue(capture_is_current(recording,'https://music.youtube.com/watch?v=example'))
        self.assertFalse(capture_is_current(recording,'https://music.youtube.com/watch?v=DIFFERENT'))

    def test_cannot_capture_without_device_or_supported_seconds(self):
        for index,seconds in [(None,20),(0,5),(9,9)]:
            with self.subTest(index=index,seconds=seconds), self.assertRaises(ValueError):
                capture_link_audio('https://youtu.be/abc',index,seconds,recorder=lambda *_a,**_k: wav_fixture())

    def test_cannot_substitute_link_text_for_waveform(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'A URL alone is not audio'):
                store_input('Music link',None,None,Path(temp),'reference')

    def test_recorded_link_is_valid_real_acoustic_input(self):
        data=wav_fixture()
        with tempfile.TemporaryDirectory() as temp:
            record=capture_link_audio('https://soundcloud.com/example/track',7,20,recorder=lambda *_a,**_k:data)
            path=store_input('Music link',None,record,Path(temp),'reference')
            self.assertEqual(path.read_bytes(),data)
            features=DSP(seconds=2).extract(path)
            self.assertIn('bpm',features)

    def test_rejects_non_wav_and_large_or_broken_capture(self):
        with self.assertRaisesRegex(ValueError,'valid WAV'):
            capture_link_audio('https://youtu.be/a',3,20,recorder=lambda *_a,**_k:b'not a recording')
        with patch('experiments.link_inputs.MAX_CAPTURE_BYTES',32):
            with self.assertRaisesRegex(ValueError,'48 MB'):
                capture_link_audio('https://youtu.be/a',3,20,recorder=lambda *_a,**_k:wav_fixture())


if __name__=='__main__': unittest.main()


class ExperimentalUpgradeTests(unittest.TestCase):
    def test_070_to_071_release_upgrade_rollback_without_stable_changes(self):
        from updater import (apply_pending_experimental, apply_pending_experimental_rollback,
                             experimental_version, stage_experimental, stage_experimental_rollback,
                             validate_package)
        root=Path(__file__).resolve().parents[1]
        base=root / 'releases' / 'experimental' / 'v0.7.0.zip'
        new=root / 'releases' / 'experimental' / 'v0.7.1.zip'
        self.assertTrue(base.exists(), 'Archived 0.7.0 release fixture must be present')
        self.assertTrue(new.exists(), 'Build 0.7.1 release before running integration tests')
        previous_bytes=base.read_bytes()
        current_bytes=new.read_bytes()
        self.assertEqual(validate_package(current_bytes,'0.7.0',channel='experimental')['version'],'0.7.1')
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)
            (dest/'version.py').write_text('APP_VERSION = "0.6.1"\n')
            (dest/'app.py').write_text('# stable app untouched\n')
            (dest/'run-windows.bat').write_text('echo stable\n')
            (dest/'user-data').mkdir()
            (dest/'user-data'/'jamendo.json').write_text('{"client_id":"fdfc0cdf"}')
            stage_experimental(dest,previous_bytes)
            apply_pending_experimental(dest)
            self.assertEqual(experimental_version(dest),'0.7.0')
            stage_experimental(dest,current_bytes)
            apply_pending_experimental(dest)
            self.assertEqual(experimental_version(dest),'0.7.1')
            self.assertTrue((dest/'experimental-build'/'experiments'/'link_inputs.py').is_file())
            self.assertTrue((dest/'experimental-build'/'live_audio.py').is_file())
            self.assertEqual(stage_experimental_rollback(dest),'0.7.0')
            self.assertEqual(apply_pending_experimental_rollback(dest),'0.7.0')
            self.assertEqual(experimental_version(dest),'0.7.0')
            self.assertEqual((dest/'app.py').read_text(),'# stable app untouched\n')
            self.assertEqual((dest/'version.py').read_text(),'APP_VERSION = "0.6.1"\n')
            self.assertEqual((dest/'user-data'/'jamendo.json').read_text(),'{"client_id":"fdfc0cdf"}')
            self.assertEqual((dest/'run-windows.bat').read_text(),'echo stable\n')
