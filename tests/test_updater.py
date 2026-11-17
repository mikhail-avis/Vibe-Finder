import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import zipfile
from pathlib import Path

from updater import UpdateError, apply_pending, check_online, installed_version, stage_package, validate_package


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

    def test_check_online_avoids_stale_github_manifest_cache(self):
        manifest = {
            "app_id": "vibe-finder", "version": "0.4.2",
            "archive_url": "https://raw.githubusercontent.com/mikhail-avis/Vibe-Finder/main/releases/v0.4.2.zip",
            "sha256": "a" * 64,
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "version.py").write_text('APP_VERSION = "0.4.1"\n')
            (root / "update-source.json").write_text(json.dumps({
                "manifest_url": "https://raw.githubusercontent.com/mikhail-avis/Vibe-Finder/main/releases/latest.json"
            }))

            class Response(io.BytesIO):
                def __init__(self, payload, url):
                    super().__init__(payload)
                    self.url = url
                def geturl(self):
                    return self.url

            requests = []
            def fake_urlopen(req, timeout):
                requests.append(req)
                return Response(json.dumps(manifest).encode("utf-8"), req.full_url)

            with patch("updater.urllib.request.urlopen", side_effect=fake_urlopen):
                first = check_online(root)
                second = check_online(root)
            self.assertFalse(first["current"])
            self.assertEqual(first["version"], "0.4.2")
            self.assertEqual(len(requests), 2)
            urls = [r.full_url for r in requests]
            self.assertNotEqual(urls[0], urls[1])
            for req in requests:
                parts = urlsplit(req.full_url)
                self.assertEqual(parts.hostname, "raw.githubusercontent.com")
                self.assertEqual(parts.path, "/mikhail-avis/Vibe-Finder/main/releases/latest.json")
                self.assertEqual(len(parse_qs(parts.query)["vf_refresh"][0]), 16)
                self.assertIn("no-cache", req.get_header("Cache-control"))

    def test_check_online_reports_remote_and_installed_version(self):
        manifest = {"app_id": "vibe-finder", "version": "0.4.1"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "version.py").write_text('APP_VERSION = "0.4.1"\n')
            (root / "update-source.json").write_text(json.dumps({
                "manifest_url": "https://raw.githubusercontent.com/mikhail-avis/Vibe-Finder/main/releases/latest.json"
            }))
            class Response(io.BytesIO):
                def geturl(self):
                    return "https://raw.githubusercontent.com/mikhail-avis/Vibe-Finder/main/releases/latest.json"
            with patch("updater.urllib.request.urlopen", return_value=Response(json.dumps(manifest).encode())):
                status = check_online(root)
            self.assertEqual(status, {"current": True, "version": "0.4.1", "remote_version": "0.4.1"})

    def test_no_update_does_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertIsNone(apply_pending(Path(t)))


if __name__ == "__main__":
    unittest.main()
