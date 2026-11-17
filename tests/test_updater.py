import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from updater import UpdateError, apply_pending, installed_version, stage_package, validate_package


def package(version="0.4.0", malicious_name=None, bad_hash=False):
    files = {
        "version.py": f'APP_VERSION = "{version}"\n'.encode(),
        "app.py": b'# updated app\n',
        "requirements.txt": b'requests>=2\n',
    }
    if malicious_name:
        files[malicious_name] = b"wrong"
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    if bad_hash:
        hashes["app.py"] = "0" * 64
    manifest = {"app_id": "vibe-finder", "version": version, "files": hashes}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
        z.writestr("release-manifest.json", json.dumps(manifest))
    return out.getvalue()


class UpdaterTests(unittest.TestCase):
    def test_preserves_personal_files_on_update(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "version.py").write_text('APP_VERSION = "0.3.0"\n')
            (root / "app.py").write_text('old app')
            (root / "requirements.txt").write_text('requests>=1\n')
            (root / "library").mkdir()
            (root / "library" / "my-track.wav").write_bytes(b'personal music')
            (root / "update-source.json").write_text('{"manifest_url":null}')
            self.assertEqual(stage_package(root, package()), "0.4.0")
            self.assertEqual(installed_version(root), "0.3.0")
            self.assertEqual(apply_pending(root), "0.4.0")
            self.assertEqual(installed_version(root), "0.4.0")
            self.assertEqual((root / "library" / "my-track.wav").read_bytes(), b'personal music')
            self.assertEqual((root / "update-source.json").read_text(), '{"manifest_url":null}')
            self.assertTrue((root / "updates" / "needs_dependencies.txt").exists())
            self.assertFalse((root / "updates" / "pending_update.zip").exists())

    def test_bad_hash_downgrade_and_unsafe_paths_rejected(self):
        for raw in (package(bad_hash=True), package(version="0.2.0"), package(malicious_name="../secret.py"), package(malicious_name="library/track.py")):
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(UpdateError):
                    validate_package(raw, "0.3.0")

    def test_no_update_does_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertIsNone(apply_pending(Path(t)))


if __name__ == "__main__":
    unittest.main()
