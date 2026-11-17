"""Experimental one-reference search: no result without decoding/audio comparison."""
from __future__ import annotations
import io
import tempfile
import unittest
from pathlib import Path

from experiments.discovery_lab import find_similar_audio


class Backend:
    name = 'mock-audio'
    def extract(self, path):
        # The fixture represents decoded audio content, independent of metadata.
        content = Path(path).read_bytes()
        if not content.startswith(b'SOUND:'):
            raise ValueError('Not valid audio fixture')
        return float(content.split(b':')[1])
    def similarity(self, a, b):
        return 1.0 - abs(a-b)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.reference = Path(self.dir.name) / 'reference.wav'
        self.reference.write_bytes(b'SOUND:0.9')
        self.rows = [
            {'id':'a','source':'Jamendo','artist':'Metadata winner','title':'Best similar match', 'audio_url':'https://host.test/a.wav','listen_url':'https://test/one','license_url':'https://cc/1'},
            {'id':'b','source':'ccMixter','artist':'Unrelated title','title':'Unrelated title', 'audio_url':'https://host.test/b.wav','listen_url':'https://test/two','license_url':'https://cc/2'},
            {'id':'c','source':'Wikimedia Commons','artist':'Silent','title':'Bad recording', 'audio_url':'https://host.test/c.wav','listen_url':'https://test/three','license_url':'https://cc/3'},
        ]

    def search(self, key, count, *, sources):
        self.assertEqual(key, 'fdfc0cdf')
        self.assertTrue(sources)
        return self.rows[:count], ['source warning']

    def download(self, track, target):
        vals={'a':b'SOUND:0.1','b':b'SOUND:0.95','c':b'broken audio'}
        target.write_bytes(vals[track['id']]);return target

    def test_one_reference_compares_actual_candidate_audio_and_orders_matches(self):
        updates=[]
        got=find_similar_audio(self.reference,Backend(), sample_count=3,
            sources=('Jamendo','ccMixter','Wikimedia Commons'), finder=self.search,
            downloader=self.download, on_progress=lambda d,t,m:updates.append((d,t,m)))
        self.assertEqual(got['sampled'],3)
        self.assertEqual(got['analysed'],2)
        self.assertEqual(got['ranked'][0]['id'],'b')
        self.assertEqual(got['ranked'][1]['id'],'a')
        self.assertEqual(len(got['skipped']),1)
        self.assertEqual(got['warnings'],['source warning'])
        self.assertEqual(updates[-1][:2],(3,3))
        self.assertTrue(all(x['score']<=1 for x in got['ranked']))
        self.assertEqual(set(Path(self.dir.name).glob('candidate_*')),set())

    def test_invalid_reference_fails_before_catalogue_access(self):
        self.reference.write_text('not-audio')
        with self.assertRaisesRegex(ValueError,'valid audio'):
            find_similar_audio(self.reference,Backend(),finder=lambda *_a,**_k:self.fail('Network called'))

    def test_invalid_selection_does_not_call_network(self):
        for kwargs in ({'sources':()}, {'sources':('Unknown provider',)}, {'sample_count':100}, {'sample_count':0}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                find_similar_audio(self.reference,Backend(),finder=lambda *_a,**_k:self.fail('Network called'),**kwargs)

    def test_backend_extract_failure_is_skipped_not_ranked(self):
        rows=[self.rows[2]]
        got=find_similar_audio(self.reference,Backend(),finder=lambda *_a,**_k:(rows,[]),downloader=self.download)
        self.assertEqual(got['ranked'],[])
        self.assertEqual(got['analysed'],0)
        self.assertEqual(len(got['skipped']),1)

    def test_no_candidates_explained_without_fabricated_matches(self):
        got=find_similar_audio(self.reference,Backend(),finder=lambda *_a,**_k:([],['No eligible audio']))
        self.assertEqual(got['sampled'],0)
        self.assertFalse(got['ranked'])
        self.assertEqual(got['warnings'],['No eligible audio'])

    def test_non_finite_similarity_is_skipped(self):
        class BadBackend(Backend):
            def similarity(self,a,b):return float('nan')
        got=find_similar_audio(self.reference,BadBackend(),finder=lambda *_a,**_k:(self.rows[:1],[]),downloader=self.download)
        self.assertEqual(got['analysed'],0)
        self.assertIn('non-finite',got['skipped'][0])

    def test_reference_can_be_actual_decoded_wav(self):
        import numpy as np
        import soundfile as sf
        from experiments.audio_models import DSP
        sr=22050
        def tone(freq):
            t=np.arange(sr*5,dtype=np.float32)/sr
            o=io.BytesIO()
            sf.write(o,0.25*np.sin(2*np.pi*freq*t),sr,format='WAV')
            return o.getvalue()
        self.reference.write_bytes(tone(220))
        reference=self.reference
        rows=self.rows[:2]
        def d(track,path):
            path.write_bytes(tone(220 if track['id']=='b' else 660));return path
        got=find_similar_audio(reference,DSP(seconds=5),finder=lambda *_a,**_k:(rows,[]),downloader=d)
        self.assertEqual(got['analysed'],2)
        self.assertTrue(all(isinstance(x['score'],float) for x in got['ranked']))

if __name__=='__main__': unittest.main()
