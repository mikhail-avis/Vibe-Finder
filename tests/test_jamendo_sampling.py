import unittest
from jamendo import find_candidates, temporary_audio, _is_trusted_audio_url

class FakeResponse:
    def __init__(self, tag): self.tag=tag
    def raise_for_status(self): pass
    def json(self):
        return {'headers':{'status':'success'},'results':[
            {'id':f'{self.tag}-{i}', 'name':f'Song {i}', 'artist_name':'Tester',
             'audiodownload_allowed': True,
             'audiodownload':f'https://mp3d.jamendo.com/audio/{self.tag}-{i}'}
            for i in range(8)
        ]}

class FakeSession:
    def get(self, url, params, **kwargs): return FakeResponse(params['tags'])

class SamplingTests(unittest.TestCase):
    def test_round_robin_does_not_favour_first_style(self):
        tracks, warnings = find_candidates('abcd1234', 6, tags=('rock','metal','jazz'), session=FakeSession())
        self.assertEqual(warnings, [])
        self.assertEqual([t['source_style'] for t in tracks],
                         ['rock','metal','jazz','rock','metal','jazz'])

    def test_second_pass_skips_seen_tracks(self):
        a, _ = find_candidates('abcd1234', 4, tags=('rock',), session=FakeSession())
        b, _ = find_candidates('abcd1234', 4, tags=('rock',), session=FakeSession(), exclude_ids={t['id'] for t in a})
        self.assertEqual(len(b), 4)
        self.assertTrue({t['id'] for t in a}.isdisjoint(t['id'] for t in b))

    def test_empty_tag_list(self):
        self.assertEqual(find_candidates('abcd1234', 5, tags=(), session=FakeSession()), ([], []))

if __name__ == '__main__': unittest.main()
