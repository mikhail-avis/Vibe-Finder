"""Safety regression tests for the separate experimental updater and stable rollback."""
import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from updater import (
    UpdateError, validate_package, stage_package, apply_pending, rollback_status,
    stage_rollback, apply_pending_rollback, stage_experimental,
    apply_pending_experimental, experimental_version, experimental_rollback_status,
    stage_experimental_rollback, apply_pending_experimental_rollback,
    remote_update_source, stage_online, check_online,
)


def make_package(version, *, channel="stable", include_experiment=True, text=None):
    files = {
        "app.py": (text or "# application\n").encode(),
        "version.py": f'APP_VERSION = "{version}"\n'.encode(),
        "updater.py": b'# placeholder for new updater\n',
        "requirements.txt": b'requests>=2\n',
    }
    if channel == "experimental" and include_experiment:
        files["experimental_landing.py"] = b'# experimental panel\n'
        files["experiments/audio_models.py"] = b'# experiment backend\n'
    manifest = {
        "app_id": "vibe-finder", "version": version,
        "channel": channel,
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for k, v in files.items():
            archive.writestr(k, v)
        archive.writestr("release-manifest.json", json.dumps(manifest))
    return output.getvalue()


class UpdateChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "version.py").write_text('APP_VERSION = "0.6.0"\n')
        (self.root / "app.py").write_text('# stable original\n')
        (self.root / "updater.py").write_text('# old updater\n')
        (self.root / "requirements.txt").write_text('requests>=2\n')
        (self.root / "user-data").mkdir()
        (self.root / "user-data" / "jamendo.json").write_text('{"client_id":"fdfc0cdf"}')
        (self.root / "library").mkdir()
        (self.root / "library" / "song.wav").write_bytes(b'music bytes')
        (self.root / "run-windows.bat").write_text('echo stable launcher\n')
        (self.root / "update-source.json").write_text(json.dumps({
            "manifest_url": "https://raw.githubusercontent.com/mikhail-avis/Vibe-Finder/main/releases/latest.json"
        }))

    def assert_stable_intact(self):
        self.assertEqual((self.root / "version.py").read_text(), 'APP_VERSION = "0.6.0"\n')
        self.assertEqual((self.root / "app.py").read_text(), '# stable original\n')
        self.assertEqual((self.root / "user-data" / "jamendo.json").read_text(), '{"client_id":"fdfc0cdf"}')
        self.assertEqual((self.root / "library" / "song.wav").read_bytes(), b'music bytes')
        self.assertEqual((self.root / "run-windows.bat").read_text(), 'echo stable launcher\n')

    def test_stable_rejects_experiment_zip(self):
        with self.assertRaisesRegex(UpdateError, 'experimental'):
            stage_package(self.root, make_package("0.7.0", channel="experimental"))
        self.assert_stable_intact()

    def test_experiment_rejects_stable_zip(self):
        with self.assertRaisesRegex(UpdateError, 'stable'):
            stage_experimental(self.root, make_package("0.6.1"))
        self.assert_stable_intact()

    def test_experiment_requires_entrypoint(self):
        with self.assertRaisesRegex(UpdateError, 'incomplete'):
            stage_experimental(self.root, make_package("0.7.0",channel="experimental",include_experiment=False))

    def test_experimental_install_keeps_stable_files_and_settings(self):
        self.assertEqual(stage_experimental(self.root, make_package("0.7.0", channel="experimental")), "0.7.0")
        self.assertEqual(apply_pending_experimental(self.root), "0.7.0")
        self.assertEqual(experimental_version(self.root), "0.7.0")
        self.assertTrue((self.root / 'experimental-build' / 'experimental_landing.py').is_file())
        self.assert_stable_intact()

    def test_second_experiment_can_rollback_without_changing_stable(self):
        stage_experimental(self.root, make_package("0.7.0", channel="experimental"))
        apply_pending_experimental(self.root)
        stage_experimental(self.root, make_package("0.7.1", channel="experimental"))
        apply_pending_experimental(self.root)
        self.assertEqual(experimental_rollback_status(self.root), "0.7.0")
        self.assertEqual(stage_experimental_rollback(self.root), "0.7.0")
        self.assertEqual(apply_pending_experimental_rollback(self.root), "0.7.0")
        self.assertEqual(experimental_version(self.root), "0.7.0")
        self.assert_stable_intact()

    def test_experimental_stage_only_does_not_install_on_click(self):
        stage_experimental(self.root, make_package("0.7.0", channel="experimental"))
        self.assertEqual(experimental_version(self.root), "0.0")
        self.assertTrue((self.root / "updates" / "pending_experimental.zip").exists())
        self.assert_stable_intact()

    def test_experimental_rollback_requires_previous_version(self):
        with self.assertRaisesRegex(UpdateError, 'No previous'):
            stage_experimental_rollback(self.root)

    def test_stable_update_rolls_back_and_preserves_personal_files(self):
        stage_package(self.root, make_package("0.6.1", text="# stable newer\n"))
        self.assertEqual(apply_pending(self.root), "0.6.1")
        self.assertEqual((self.root / 'app.py').read_text(), '# stable newer\n')
        self.assertEqual(rollback_status(self.root), {'installed': '0.6.1', 'restore':'0.6.0'})
        self.assertEqual(stage_rollback(self.root), "0.6.0")
        self.assertEqual(apply_pending_rollback(self.root), "0.6.0")
        self.assertIsNone(rollback_status(self.root))
        self.assert_stable_intact()

    def test_rollback_blocked_if_current_app_modified_after_update(self):
        stage_package(self.root, make_package("0.6.1"))
        apply_pending(self.root)
        (self.root / 'app.py').write_text('# modified after update\n')
        self.assertIsNone(rollback_status(self.root))
        with self.assertRaisesRegex(UpdateError, 'changed after'):
            stage_rollback(self.root)

    def test_corrupt_backup_cannot_be_staged(self):
        stage_package(self.root, make_package("0.6.1"))
        apply_pending(self.root)
        (self.root / "updates" / "rollback.zip").write_bytes(b'bad archive')
        self.assertIsNone(rollback_status(self.root))
        with self.assertRaises(UpdateError):
            stage_rollback(self.root)

    def test_new_update_rejected_if_rollback_pending(self):
        stage_package(self.root, make_package("0.6.1"))
        apply_pending(self.root)
        stage_rollback(self.root)
        with self.assertRaisesRegex(UpdateError, 'rollback is already staged'):
            stage_package(self.root, make_package("0.6.2"))

    def test_rollback_cleans_only_installed_files_missing_prior_version(self):
        raw = make_package('0.6.1')
        stage_package(self.root, raw)
        apply_pending(self.root)
        stage_rollback(self.root)
        apply_pending_rollback(self.root)
        self.assert_stable_intact()

    def test_experimental_feed_derived_from_trusted_update_source(self):
        self.assertEqual(remote_update_source(self.root, channel='experimental'),
            'https://raw.githubusercontent.com/mikhail-avis/Vibe-Finder/main/releases/experimental/latest.json')
        self.assertEqual(remote_update_source(self.root, channel='stable'),
            'https://raw.githubusercontent.com/mikhail-avis/Vibe-Finder/main/releases/latest.json')

    def test_online_stage_wrong_channel_blocks_experiment(self):
        release = {"current": False, "version": "0.7.0", "channel":"experimental",
                   "archive_url":"https://raw.githubusercontent.com/x.zip",
                   "host":"raw.githubusercontent.com","sha256":"a"*64}
        with patch('updater._get_url', return_value=b'x'):
            with self.assertRaises(UpdateError):
                stage_online(self.root, release, channel='stable')

    def test_online_feed_does_not_accept_stable_as_experimental(self):
        feed = {'app_id':'vibe-finder','version':'0.7.0','archive_url':'https://raw.githubusercontent.com/x.zip', 'sha256':'a'*64}
        with patch('updater._get_url', return_value=json.dumps(feed).encode()):
            with self.assertRaisesRegex(UpdateError, 'channel'):
                check_online(self.root, channel='experimental')

    def test_no_published_experiment_returns_none_on_404(self):
        from urllib.error import HTTPError
        with patch('updater._get_url', side_effect=HTTPError('url',404,'not found',{},None)):
            self.assertIsNone(check_online(self.root, channel='experimental'))

if __name__ == '__main__': unittest.main()
