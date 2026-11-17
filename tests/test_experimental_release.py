"""Experimental release builds do not mutate the stable installation."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from updater import apply_pending_experimental, stage_experimental, installed_version, validate_package

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'build_experimental_release.py'

class ExperimentalReleaseTests(unittest.TestCase):
    def test_build_package_has_experimental_channel_and_fingerprint(self):
        spec = importlib.util.spec_from_file_location('experimental_builder', SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in mod.FILES:
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((mod.ROOT / name).read_bytes())
            package, feed = mod.build('example', 'vibe-finder', root)
            manifest = validate_package(package.read_bytes(), '0.0', channel='experimental')
            self.assertEqual(manifest['version'], '0.7.0')
            self.assertEqual(manifest['channel'], 'experimental')
            self.assertIn('experiments/audio_models.py', manifest['files'])
            data = json.loads(feed.read_text())
            self.assertEqual(data['channel'], 'experimental')
            self.assertEqual(data['sha256'], hashlib.sha256(package.read_bytes()).hexdigest())
            with zipfile.ZipFile(package) as archive:
                self.assertIn('experimental_landing.py', archive.namelist())
            # A release may be installed without touching stable files.
            (root / 'version.py').write_text('APP_VERSION = "0.6.1"\n')
            (root / 'app.py').write_text('# live stable application\n')
            stage_experimental(root, package.read_bytes())
            apply_pending_experimental(root)
            self.assertEqual(installed_version(root), '0.6.1')
            self.assertEqual((root / 'app.py').read_text(), '# live stable application\n')
            self.assertEqual(installed_version(root / 'experimental-build'), '0.7.0')

    def test_experiment_ui_works_without_importing_clap_or_essentia(self):
        from experimental_landing import backend_for
        from experiments.audio_models import DSP, BackendUnavailable
        self.assertIsInstance(backend_for('DSP (stable algorithm)', ''), DSP)
        with self.assertRaises(BackendUnavailable):
            backend_for('CLAP (experimental)', '')
        with self.assertRaises(BackendUnavailable):
            backend_for('Essentia (experimental)', '')

if __name__ == '__main__': unittest.main()
