"""Clear diagnostic states and recording-time feedback without Windows hardware."""
import unittest
from unittest.mock import patch

import live_audio

class SignalFeedbackTests(unittest.TestCase):
    def test_silence_is_not_success(self):
        state = live_audio.describe_signal(0.0, float('-inf'))
        self.assertEqual(state['status'], 'no_sound')
        self.assertIn('No sound', state['headline'])

    def test_low_volume_is_not_called_good(self):
        state = live_audio.describe_signal(.02, -48.)
        self.assertEqual(state['status'], 'too_quiet')
        self.assertIn('quiet', state['headline'])

    def test_usable_volume_has_actionable_next_step(self):
        state = live_audio.describe_signal(.26, -22.)
        self.assertEqual(state['status'], 'sound_detected')
        self.assertIn('Listen Live', state['next_step'])
        self.assertNotIn('matched', state['detail'].lower())

    def test_clipping_is_flagged(self):
        self.assertEqual(live_audio.describe_signal(.99, -10., .05)['status'],
                         'possibly_distorted')

    def test_excess_average_level_is_flagged(self):
        self.assertEqual(live_audio.describe_signal(.86, -4.)['status'],
                         'possibly_distorted')

    def test_quick_check_requests_one_second_and_records_elapsed(self):
        fake = b'\x00\x10' * 48000
        device = {'name':'Bluetooth Headphones', 'index':7}
        def capture(seconds, selected, timings=None):
            self.assertEqual(seconds, 1)
            self.assertEqual(selected, 7)
            if timings is not None:
                timings.update({'setup_s': 0.06, 'capture_s': 1.0})
            return fake, 48000, 1, device
        with patch.object(live_audio, '_read_samples', side_effect=capture):
            status = live_audio.test_output_device(7)
        self.assertEqual(status['sample_seconds'], 1)
        self.assertGreaterEqual(status['elapsed_seconds'], 0)
        self.assertEqual(status['status'], 'sound_detected')
        self.assertEqual(status['setup_seconds'], 0.06)
        self.assertEqual(status['capture_seconds'], 1.0)

    def test_quick_check_passes_silent_result_through(self):
        device = {'name':'Silent Device', 'index':3}
        with patch.object(live_audio, '_read_samples', return_value=(b'\x00\x00'*48000, 48000, 1, device)):
            status = live_audio.test_output_device(3)
        self.assertEqual(status['status'], 'no_sound')
        self.assertFalse(status['audible'])

if __name__=='__main__':
    unittest.main()
