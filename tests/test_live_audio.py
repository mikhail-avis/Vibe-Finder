"""Mocked Windows WASAPI tests: independent of actual audio hardware."""
import io
import math
import struct
import sys
import types
import unittest
import wave
from unittest.mock import patch

import live_audio


class FakeStream:
    def __init__(self, channels, loud=True):
        self.channels = channels
        self.loud = loud
        self.frames_read = 0
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, frames, exception_on_overflow=False):
        self.frames_read += frames
        level = 4000 if self.loud else 0
        return struct.pack('<h', level) * frames * self.channels


class FakeDriver:
    outputs = [
        {'index': 3, 'name': 'Speakers (Realtek) [Loopback]', 'maxInputChannels': 2,
         'defaultSampleRate': 48000.0, 'isLoopbackDevice': True},
        {'index': 9, 'name': 'Bluetooth Headphones [Loopback]', 'maxInputChannels': 2,
         'defaultSampleRate': 44100.0, 'isLoopbackDevice': True},
        {'index': 11, 'name': 'Microphone (not playback)', 'maxInputChannels': 1,
         'defaultSampleRate': 44100.0, 'isLoopbackDevice': False},
    ]
    default_index = 3
    is_loud = True
    opened = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def get_default_wasapi_loopback(self):
        return next(d for d in self.outputs if d['index'] == self.default_index)
    def get_loopback_device_info_generator(self):
        return iter(self.outputs)
    def open(self, **kwargs):
        self.opened.append(kwargs)
        return FakeStream(kwargs['channels'], self.is_loud)


class LiveAudioTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.driver.opened = []
        self.fake_pa = types.SimpleNamespace(PyAudio=lambda: self.driver, paInt16=8)
        self.p1 = patch.object(live_audio.sys, 'platform', 'win32')
        self.p2 = patch.dict(sys.modules, {'pyaudiowpatch': self.fake_pa})
        self.p1.start(); self.p2.start()
        self.addCleanup(self.p2.stop); self.addCleanup(self.p1.stop)

    def test_list_playback_excludes_microphone_and_marks_default(self):
        devices = live_audio.list_output_devices()
        self.assertEqual([d['index'] for d in devices], [3, 9])
        self.assertTrue(devices[0]['is_default'])
        self.assertFalse(devices[1]['is_default'])

    def test_selected_bluetooth_loopback_captured_as_wav(self):
        audio = live_audio.capture_output(3, device_index=9)
        self.assertEqual(self.driver.opened[0]['input_device_index'], 9)
        with wave.open(io.BytesIO(audio)) as wav:
            self.assertEqual(wav.getframerate(), 44100)
            self.assertEqual(wav.getnchannels(), 2)
            self.assertEqual(wav.getnframes(), 3 * 44100)

    def test_default_device_selected_without_explicit_index(self):
        live_audio.capture_output(3)
        self.assertEqual(self.driver.opened[0]['input_device_index'], 3)

    def test_disconnected_device_is_clear_error(self):
        with self.assertRaisesRegex(live_audio.CaptureError, 'disconnected'):
            live_audio.capture_output(3, device_index=73)

    def test_silent_test_returns_diagnostic_not_failure(self):
        self.driver.is_loud = False
        signal = live_audio.test_output_device(9)
        self.assertFalse(signal['audible'])
        self.assertEqual(signal['peak_percent'], 0.0)
        with self.assertRaisesRegex(live_audio.CaptureError, 'No audible sound'):
            live_audio.capture_output(3, device_index=9)

    def test_capture_never_opens_microphone(self):
        with self.assertRaises(live_audio.CaptureError):
            live_audio.capture_output(3, device_index=11)
        self.assertEqual(self.driver.opened, [])

    def test_unsupported_platform(self):
        with patch.object(live_audio.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(live_audio.CaptureError, 'Windows'):
                live_audio.list_output_devices()


if __name__ == '__main__':
    unittest.main()
