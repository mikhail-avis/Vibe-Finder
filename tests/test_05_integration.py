"""Synthetic audio tests, real DSP, mocked network; no Jamendo credentials."""
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from functools import partial

import numpy as np
import soundfile as sf

from adaptive_discovery import search_budgets, focus_styles
from candidate_pipeline import FINGERPRINT_VERSION, scan_candidates
from engine import GROUPS, analyse, compare
from jamendo import find_candidates, temporary_audio, JamendoError
from updater import validate_package, stage_package, apply_pending, installed_version


class Response:
    status_code=200
    headers={}
    def __init__(self,url,rows=None,audio=None):
        self.url=url; self.rows=rows; self.audio=audio
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def raise_for_status(self):pass
    def json(self):return {'headers':{'status':'success'},'results':self.rows}
    def iter_content(self,_):
        yield self.audio


class MockJamendo:
    def __init__(self, audio):
        self.audio=audio
        self.lock=threading.Lock()
        self.called=[]
    def get(self,url,**kwargs):
        with self.lock: self.called.append(url)
        if url.endswith('/tracks/'):
            tag=kwargs['params']['tags']
            return Response(url,rows=[{'id':f'{tag}-{i}', 'name':f'Licensed {i}',
                 'artist_name':'Synth', 'audiodownload_allowed':True,
                 'audiodownload': f'https://mp3d.jamendo.com/track/{tag}-{i}',
                 'shareurl':f'https://www.jamendo.com/track/{tag}-{i}'} for i in range(4)])
        if url.startswith('https://mp3d.jamendo.com/track/'):
            return Response(url,audio=self.audio)
        raise AssertionError(f'Unexpected HTTP: {url}')


class Integration05Tests(unittest.TestCase):
    def synthetic_audio(self, seconds=4):
        sr=22050
        t=np.arange(int(sr*seconds), dtype=np.float32)/sr
        wave=(0.18*np.sin(2*np.pi*85*t) + .1*np.sin(2*np.pi*225*t)).astype(np.float32)
        io_file=io.BytesIO();sf.write(io_file,wave,sr,format='WAV')
        return io_file.getvalue()

    def test_broad_to_adaptive_real_audio_pipeline_and_cache(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); data=self.synthetic_audio();(base/'reference.wav').write_bytes(data)
            reference=analyse(base/'reference.wav')
            session=MockJamendo(data)
            first_budget, remaining=search_budgets(8)
            initial,warnings=find_candidates('abcd1234',first_budget,
                            tags=('rock','metal','jazz'),session=session)
            self.assertEqual(len(initial), first_budget)
            self.assertEqual(warnings, [])
            def downloader(track,dest):return temporary_audio(track,dest,session=session)
            cache=base/'user-data'/'fingerprints.json'
            opts=dict(download_workers=2,analysis_workers=2, downloader=downloader,
                      analysis_executor_cls=ThreadPoolExecutor)
            first=list(scan_candidates(initial,reference,{g:1 for g in GROUPS},cache,**opts))
            self.assertEqual(len(first),first_budget)
            self.assertTrue(all(x['match'] for x in first))
            matching=[x['match'] for x in first]
            self.assertGreater(min(x['score'] for x in matching), 90)
            focus=focus_styles(matching,('rock','metal','jazz'))
            self.assertTrue(focus)
            further,_=find_candidates('abcd1234',remaining,tags=focus,session=session,
                                      exclude_ids={t['id'] for t in initial})
            self.assertTrue({t['id'] for t in further}.isdisjoint(x['id'] for x in initial))
            next_results=list(scan_candidates(further,reference,{g:1 for g in GROUPS},cache,**opts))
            self.assertEqual(len(next_results),remaining)
            count_before=sum('mp3d.jamendo.com/track' in url for url in session.called)
            again=list(scan_candidates(initial,reference,{g:1 for g in GROUPS},cache,**opts))
            self.assertTrue(all(x['match']['cached'] for x in again))
            self.assertEqual(sum('mp3d.jamendo.com/track' in url for url in session.called),count_before)
            self.assertNotIn('RIFF',cache.read_text())
            self.assertNotIn('abcd1234',cache.read_text())
            self.assertEqual(FINGERPRINT_VERSION,'dsp50-v2')

    def test_enhanced_features_are_finite_and_identical_reference_is_100(self):
        with tempfile.TemporaryDirectory() as td:
            file=Path(td)/'test.wav';file.write_bytes(self.synthetic_audio(seconds=6))
            features=analyse(file)
            self.assertEqual(len(features['Rhythm']),37)
            self.assertEqual(len(features['Bass']),9)
            self.assertTrue(all(np.isfinite(n) for group in GROUPS for n in features[group]))
            score=compare(features,features)
            self.assertEqual(score['score'],100.0)
            self.assertEqual(set(score['components']),set(GROUPS))

    def test_update_041_to_05_preserves_personal_content(self):
        root=Path(__file__).resolve().parents[1]
        import subprocess,sys
        subprocess.run([sys.executable,str(root/'scripts'/'build_release.py'),
                        '--owner','mikhail-avis','--repo','Vibe-Finder'],check=True,cwd=root,
                       stdout=subprocess.DEVNULL)
        package=(root/'releases'/'v0.5.1.zip').read_bytes()
        manifest=validate_package(package,'0.4.1')
        self.assertIn('adaptive_discovery.py',manifest['files'])
        self.assertIn('history.py',manifest['files'])
        with tempfile.TemporaryDirectory() as td:
            installed=Path(td)
            (installed/'version.py').write_text('APP_VERSION = "0.4.1"\n')
            (installed/'app.py').write_text('old app')
            (installed/'library').mkdir()
            (installed/'library'/'my-sound.wav').write_bytes(b'keep raw music!')
            (installed/'user-data').mkdir()
            (installed/'user-data'/'jamendo.json').write_text('{"client_id":"private"}')
            (installed/'update-source.json').write_text('{"manifest_url":"unchanged"}')
            self.assertEqual(stage_package(installed,package),'0.5.1')
            self.assertEqual(installed_version(installed),'0.4.1')
            self.assertEqual(apply_pending(installed),'0.5.1')
            self.assertEqual(installed_version(installed),'0.5.1')
            self.assertEqual((installed/'library'/'my-sound.wav').read_bytes(),b'keep raw music!')
            self.assertEqual((installed/'user-data'/'jamendo.json').read_text(),'{"client_id":"private"}')
            self.assertEqual((installed/'update-source.json').read_text(),'{"manifest_url":"unchanged"}')
            self.assertTrue((installed/'adaptive_discovery.py').exists())
            self.assertTrue((installed/'history.py').exists())

if __name__=='__main__':unittest.main()
