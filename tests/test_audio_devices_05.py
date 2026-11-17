"""Windows audio-output mocks: no mic or Bluetooth hardware needed."""
import io
import struct
import sys
import types
import unittest
import wave
from unittest.mock import patch
import live_audio


class FakeStream:
    def __init__(self,channels,loud):self.channels=channels;self.loud=loud
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def read(self,frames,exception_on_overflow=False):
        return struct.pack('<h',4000 if self.loud else 0)*frames*self.channels


class FakeDriver:
    loud=True
    def __init__(self):self.opened=[]
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def get_default_wasapi_loopback(self):return self.devices[0]
    @property
    def devices(self):return [
        {'index':3,'name':'Speakers [Loopback]','defaultSampleRate':48000,
         'maxInputChannels':2,'isLoopbackDevice':True},
        {'index':9,'name':'Bluetooth Headphones [Loopback]','defaultSampleRate':44100,
         'maxInputChannels':2,'isLoopbackDevice':True},
        {'index':11,'name':'Mic','defaultSampleRate':48000,
         'maxInputChannels':1,'isLoopbackDevice':False},
    ]
    def get_loopback_device_info_generator(self):return iter(self.devices)
    def open(self,**args):self.opened.append(args);return FakeStream(args['channels'],self.loud)


class LiveAudio05Tests(unittest.TestCase):
    def setUp(self):
        self.driver=FakeDriver()
        self.p1=patch.object(live_audio.sys,'platform','win32')
        self.p2=patch.dict(sys.modules,{'pyaudiowpatch':types.SimpleNamespace(
              PyAudio=lambda:self.driver,paInt16=8)})
        self.p1.start();self.p2.start()
        self.addCleanup(self.p2.stop);self.addCleanup(self.p1.stop)

    def test_only_playback_devices_listed(self):
        devices=live_audio.list_output_devices()
        self.assertEqual([d['index'] for d in devices],[3,9])
        self.assertTrue(devices[0]['is_default'])

    def test_bluetooth_capture_reads_selected_output(self):
        raw=live_audio.capture_output(3,device_index=9)
        with wave.open(io.BytesIO(raw)) as reader:
            self.assertEqual(reader.getnchannels(),2)
            self.assertEqual(reader.getframerate(),44100)
        self.assertEqual(self.driver.opened[0]['input_device_index'],9)

    def test_silent_device_returns_diagnostic(self):
        self.driver.loud=False
        self.assertFalse(live_audio.test_output_device(9)['audible'])
        with self.assertRaisesRegex(live_audio.CaptureError,'No audible sound'):
            live_audio.capture_output(3,device_index=9)

    def test_microphone_not_used(self):
        with self.assertRaises(live_audio.CaptureError):
            live_audio.capture_output(3,device_index=11)
        self.assertEqual(self.driver.opened,[])

    def test_disconnected_device_prompts_refresh(self):
        with self.assertRaisesRegex(live_audio.CaptureError,'disconnected'):
            live_audio.capture_output(3,device_index=77)

if __name__=='__main__':unittest.main()
