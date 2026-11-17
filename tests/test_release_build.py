import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from updater import validate_package

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'build_release.py'

class ReleaseBuildTests(unittest.TestCase):
    def test_manifest_zip_and_feed(self):
        spec = importlib.util.spec_from_file_location('release_builder', SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in mod.RELEASE_FILES:
                (root / name).write_bytes((mod.ROOT / name).read_bytes())
            package, feed = mod.build('example-user', 'vibe-finder', root)
            validated = validate_package(package.read_bytes(), '0.2.0')
            self.assertEqual(validated['version'], '0.5.1')
            data = json.loads(feed.read_text())
            self.assertEqual(data['app_id'], 'vibe-finder')
            self.assertEqual(data['archive_url'], 'https://raw.githubusercontent.com/example-user/vibe-finder/main/releases/v0.5.1.zip')
            with zipfile.ZipFile(package) as z:
                self.assertEqual(z.read('app.py'), (root / 'app.py').read_bytes())

if __name__ == '__main__':
    unittest.main()
