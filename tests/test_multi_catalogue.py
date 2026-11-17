"""Multi-source discovery with actual waveform extraction using mock HTTP responses."""
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from threading import Lock
from unittest.mock import patch

import numpy as np
import soundfile as sf

import catalogues
from catalogues import (find_ccmixter, find_commons, discover_across_sources,
                        trusted_audio_url)
from downloaders import download_track_audio, AudioDownloadError
from engine import GROUPS, analyse
from candidate_pipeline import scan_candidates


class FakeResponse:
    def __init__(self, url, *, data=None, raw=None, status_code=200, location=None):
        self.url = url
        self.data = data
        self.raw = raw
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
    def json(self):
        return self.data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")
    def iter_content(self, size):
        yield self.raw
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def synth(freq):
    sr=22050
    t=np.arange(sr * 4, dtype=np.float32) / sr
    y=(.22*np.sin(2*np.pi*freq*t) + .11*np.sin(2*np.pi*2*freq*t)).astype('float32')
    file=io.BytesIO()
    sf.write(file, y, sr, format='WAV')
    return file.getvalue()


class FakeCatalogues:
    def __init__(self):
        self.audio = {"ccmixter.org": synth(75), "upload.wikimedia.org": synth(185)}
        self.download_calls=[]
        self.lock=Lock()
    def get(self, url, **kwargs):
        p = kwargs.get('params') or {}
        if url == catalogues.CCMIXTER_API:
            tag=p.get('tags', 'electronic')
            return FakeResponse(url, data=[
                {"upload_id": "11", "upload_name": "Low Pulse", "user_name": "DJ-Alpha",
                 "license_url": "https://creativecommons.org/licenses/by/4.0/",
                 "files": [{"download_url":"https://ccmixter.org/content/beat.wav"}]},
                {"upload_id": "12", "upload_name": "No audio download", "user_name": "An Artist",
                 "license_url": "https://creativecommons.org/licenses/by/4.0/"},
                {"upload_id": "13", "upload_name": "No license", "user_name": "Artist",
                 "files": [{"download_url":"https://ccmixter.org/content/unknown.wav"}]},
            ])
        if url == catalogues.COMMONS_API:
            return FakeResponse(url, data={"query": {"pages": {
                "123": {"ns":6, "pageid":123, "title":"File:Melody.wav", "index":1,
                        "imageinfo":[{"url":"https://upload.wikimedia.org/wikipedia/commons/test/Melody.wav",
                                      "descriptionurl":"https://commons.wikimedia.org/wiki/File:Melody.wav",
                                      "size": 200000, "mediatype": "AUDIO", "mime":"audio/wav",
                                      "extmetadata":{"LicenseShortName":{"value":"CC BY-SA 4.0"},
                                                     "LicenseUrl":{"value":"https://creativecommons.org/licenses/by-sa/4.0/"},
                                                     "ObjectName":{"value":"Melody"},
                                                     "Artist":{"value":"<a>Example Musician</a>"}}}]},
                "456": {"ns": 6, "pageid": 456, "title": "File:Unsupported.ogg", "index":2,
                        "imageinfo":[{"url":"https://upload.wikimedia.org/wikipedia/commons/large.ogg",
                                      "size": 50*1024*1024,
                                      "extmetadata":{"LicenseShortName":{"value":"CC BY"},
                                                     "LicenseUrl":{"value":"https://creativecommons.org/licenses/by/4.0/"}}}]}
            }}})
        if url.startswith('https://ccmixter.org/content/') or url.startswith('https://upload.wikimedia.org/'):
            with self.lock:
                self.download_calls.append(url)
            key='ccmixter.org' if 'ccmixter' in url else 'upload.wikimedia.org'
            return FakeResponse(url, raw=self.audio[key])
        raise AssertionError(f"Unexpected URL {url}")


class SourcesTests(unittest.TestCase):
    def test_only_explicit_licensed_downloadable_audio(self):
        f=FakeCatalogues()
        c,w = find_ccmixter(5, tags=('electronic',), session=f)
        m,z = find_commons(5, tags=('ambient',), session=f)
        self.assertFalse(w+z)
        self.assertEqual([(x['source'],x['id']) for x in c+m], [('ccMixter','ccm:11'), ('Wikimedia Commons','commons:123')])
        self.assertEqual(c[0]['title'], 'Low Pulse')
        self.assertEqual(m[0]['artist'], 'Example Musician')

    def test_reject_untrusted_or_non_audio_urls(self):
        self.assertFalse(trusted_audio_url('http://ccmixter.org/content/t.mp3','ccMixter'))
        self.assertFalse(trusted_audio_url('https://ccmixter.org.evil.test/t.mp3','ccMixter'))
        self.assertFalse(trusted_audio_url('https://ccmixter.org/content/a.json','ccMixter'))
        self.assertFalse(trusted_audio_url('https://upload.wikimedia.org@evil.test/t.ogg','Wikimedia Commons'))
        self.assertTrue(trusted_audio_url('https://upload.wikimedia.org/wikipedia/commons/t.ogg','Wikimedia Commons'))

    def test_reject_bad_redirect_before_request(self):
        class Redirect:
            def __init__(self): self.calls=[]
            def get(self,url,**kwargs):
                self.calls.append(url)
                return FakeResponse(url,status_code=302,location='https://attacker.invalid/audio.mp3')
        mock=Redirect()
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'candidate.mp3'
            with self.assertRaisesRegex(AudioDownloadError, 'Redirect to untrusted'):
                download_track_audio({'source':'ccMixter','audio_url':'https://ccmixter.org/content/song.mp3'}, dest, session=mock)
            self.assertEqual(len(mock.calls),1)
            self.assertFalse(dest.exists())

    def test_multi_source_real_audio_analysis_and_fingerprint_cache(self):
        fake=FakeCatalogues()
        # Stub existing Jamendo discovery only to avoid hitting its real API.
        def jamendo(client_id,*,max_tracks,tags,exclude_ids,session):
            return [], []
        with patch.object(catalogues, 'find_jamendo', side_effect=jamendo):
            candidates,warnings=discover_across_sources('fdfc0cdf', max_tracks=6,
                                                         sources=('Jamendo','ccMixter','Wikimedia Commons'),
                                                         tags=('electronic','ambient'),session=fake)
        self.assertEqual(len(candidates),2)
        self.assertEqual(len(warnings),0)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            (path/'reference.wav').write_bytes(fake.audio['ccmixter.org'])
            reference=analyse(path/'reference.wav')
            weights={g:1 for g in GROUPS}
            opts={'download_workers':2,'analysis_workers':2,
                  'downloader':partial(download_track_audio,session=fake),
                  'analysis_executor_cls':ThreadPoolExecutor}
            events=list(scan_candidates(candidates,reference,weights,path/'features.json',**opts))
            self.assertEqual(len(events),2)
            self.assertTrue(all(event['match'] for event in events), events)
            scored=sorted((e['match'] for e in events),key=lambda e:-e['score'])
            self.assertEqual(scored[0]['source'],'ccMixter')
            self.assertAlmostEqual(scored[0]['score'],100,delta=.1)
            self.assertLess(scored[1]['score'],scored[0]['score'])
            # No matching is allowed for a raw metadata response; every scored
            # recording above required a successful real audio download.
            downloads=len(fake.download_calls)
            cached=list(scan_candidates(candidates,reference,weights,path/'features.json',**opts))
            self.assertTrue(all(x['match']['cached'] for x in cached))
            self.assertEqual(len(fake.download_calls),downloads)
            self.assertNotIn('RIFF',(path/'features.json').read_text())

    def test_no_source_and_excluded_ids(self):
        f=FakeCatalogues()
        self.assertEqual(discover_across_sources('abcd1234',4,sources=(),session=f)[0],[])
        out,_=find_commons(1,tags=('rock',),exclude_ids={'commons:123'},session=f)
        self.assertEqual(out,[])


if __name__=='__main__':
    unittest.main()
