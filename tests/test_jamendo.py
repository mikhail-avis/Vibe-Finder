import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from jamendo import (JamendoError, _is_trusted_audio_url,
                     find_candidates, temporary_audio, valid_client_id, test_connection)
from live_audio import CaptureError, capture_output


class FakeResponse:
    def __init__(self, rows=None, url="https://mp3d.jamendo.com/test", chunks=None, ok=True,
                 status_code=200, headers=None):
        self.rows = rows if rows is not None else []
        self.url = url
        self.chunks = chunks or [b"a" * 2000]
        self.ok = ok
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if not self.ok:
            from requests import HTTPError
            raise HTTPError("rejected")

    def json(self):
        return {"headers": {"status": "success"}, "results": self.rows}

    def iter_content(self, count):
        yield from self.chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class JamendoTests(unittest.TestCase):
    def test_client_id_validation(self):
        self.assertTrue(valid_client_id('abcd1234'))
        self.assertFalse(valid_client_id('http://fake.site'))
        self.assertFalse(valid_client_id('bad'))

    def test_rejects_disallowed_and_unsafe_audio(self):
        permitted = {"id": 2, "audiodownload_allowed": True,
                     "audiodownload": "https://mp3d.jamendo.com/audio", "name": "Test",
                     "artist_name": "Example", "shareurl": "https://www.jamendo.com/track/2"}
        rows = [
            {"id": 1, "audiodownload_allowed": False, "audiodownload": "https://mp3d.jamendo.com/audio"},
            permitted,
            {"id": 3, "audiodownload_allowed": True, "audiodownload": "https://evil.test/file"}
        ]
        session = Mock(get=Mock(return_value=FakeResponse(rows=rows)))
        matches, errors = find_candidates('abcd1234', session=session, tags=("electronic",), max_tracks=8)
        self.assertEqual([item['id'] for item in matches], ['2'])
        self.assertEqual(errors, [])
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(session.get.call_args.kwargs['params']['include'], 'licenses+musicinfo')

    def test_download_host_restrictions(self):
        for url in ('https://jamendo.com/a', 'https://mp3d.jamendo.com/a'):
            self.assertTrue(_is_trusted_audio_url(url))
        for url in ('http://jamendo.com/a', 'https://jamendo.com.evil.org/a',
                    'https://evil.com/a', 'file:///etc/passwd'):
            self.assertFalse(_is_trusted_audio_url(url))

    def test_connection_checks_metadata_without_audio_download(self):
        rows = [{"audiodownload_allowed": True,
                 "audiodownload": "https://mp3d.jamendo.com/a"},
                {"audiodownload_allowed": False, "audiodownload": ""}]
        session = Mock(get=Mock(return_value=FakeResponse(rows=rows)))
        self.assertEqual(test_connection("abcd1234", session=session),
                         {"returned": 2, "downloadable": 1})
        self.assertEqual(session.get.call_count, 1)

    def test_rejects_untrusted_redirect_before_request(self):
        first = FakeResponse(status_code=302, headers={"Location": "https://evil.com/steal"})
        session = Mock(get=Mock(return_value=first))
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "test.mp3"
            with self.assertRaises(JamendoError):
                temporary_audio({"audio_url": "https://mp3d.jamendo.com/a"}, target, session=session)
            self.assertEqual(session.get.call_count, 1)
            self.assertFalse(target.exists())

    def test_rejects_excess_redirects(self):
        response = FakeResponse(status_code=302, headers={"Location": "/again"})
        session = Mock(get=Mock(return_value=response))
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(JamendoError):
                temporary_audio({"audio_url": "https://mp3d.jamendo.com/a"}, Path(td) / "test.mp3", session=session)
            self.assertEqual(session.get.call_count, 4)

    def test_partial_download_cleanup_after_network_failure(self):
        import requests
        def broken_chunks(_):
            yield b"a" * 2048
            raise requests.ConnectionError("connection dropped")
        response = FakeResponse()
        response.iter_content = broken_chunks
        session = Mock(get=Mock(return_value=response))
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "partial.mp3"
            with self.assertRaises(JamendoError):
                temporary_audio({"audio_url": "https://mp3d.jamendo.com/a"}, target, session=session)
            self.assertFalse(target.exists())

    def test_download_cleanup_unsafe_redirect(self):
        track = {'audio_url': 'https://mp3d.jamendo.com/audio'}
        session = Mock(get=Mock(return_value=FakeResponse(url='https://evil.com/data')))
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'test.mp3'
            with self.assertRaises(JamendoError):
                temporary_audio(track, target, session=session)
            self.assertFalse(target.exists())

    def test_download_streams_to_temporary_path(self):
        track = {'audio_url': 'https://mp3d.jamendo.com/audio'}
        session = Mock(get=Mock(return_value=FakeResponse()))
        with tempfile.TemporaryDirectory() as td:
            target = temporary_audio(track, Path(td)/'test.mp3', session=session)
            self.assertEqual(target.stat().st_size, 2000)

    def test_live_capture_requires_windows(self):
        from unittest.mock import patch
        with patch('live_audio.sys.platform', 'linux'):
            with self.assertRaises(CaptureError):
                capture_output(20)

if __name__ == '__main__':
    unittest.main()
