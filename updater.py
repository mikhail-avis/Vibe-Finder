"""Careful, in-place Vibe Finder updates. Standard-library only.

The app stages a complete ZIP. The Windows launcher applies it *before* starting
Streamlit, which avoids replacing imported files while the app is running.
Only a release from a trusted source should be installed. The hash in the
release manifest checks integrity, not the identity of a manually supplied ZIP.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

APP_ID = "vibe-finder"
MANIFEST = "release-manifest.json"
PENDING = "pending_update.zip"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 120 * 1024 * 1024
MAX_FILES = 250
PROTECTED = {"release-manifest.json", "update-source.json", "run-windows.bat"}
PROTECTED_FOLDERS = {"library", ".venv", "updates", "user-data", "__pycache__", ".git"}
ALLOWED_EXTENSIONS = {".py", ".md", ".txt"}


class UpdateError(ValueError):
    """A release is invalid, not newer, or cannot be installed."""


def _version(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise UpdateError("Invalid version identifier")
    return tuple(map(int, value.split(".")))


def installed_version(root: Path) -> str:
    source = (root / "version.py").read_text(encoding="utf-8")
    found = re.search(r'^APP_VERSION\s*=\s*"([0-9.]+)"\s*$', source, re.M)
    if not found:
        raise UpdateError("Couldn't read installed version")
    _version(found.group(1))
    return found.group(1)


def _safe_relpath(name: str) -> bool:
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        return False
    parts = PurePosixPath(name).parts
    if not parts or any(p in ("", ".", "..") for p in name.split("/")):
        return False
    if parts[0].casefold() in PROTECTED_FOLDERS or name.casefold() in PROTECTED:
        return False
    return (all(not part.startswith(".") for part in parts)
            and all(part.casefold() not in PROTECTED_FOLDERS for part in parts)
            and PurePosixPath(name).suffix.lower() in ALLOWED_EXTENSIONS)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_package(raw: bytes, current_version: str) -> dict:
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise UpdateError("Update archive is too large")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            entries = package.infolist()
            if len(entries) > MAX_FILES or len({x.filename for x in entries}) != len(entries):
                raise UpdateError("Invalid or duplicate release entries")
            if any((stat.S_IFMT(x.external_attr >> 16) == stat.S_IFLNK) for x in entries):
                raise UpdateError("Symbolic links are not allowed in releases")
            if sum(x.file_size for x in entries) > MAX_UNCOMPRESSED_BYTES:
                raise UpdateError("Expanded update size exceeds limit")
            if MANIFEST not in package.namelist():
                raise UpdateError("Not a Vibe Finder update ZIP (missing manifest)")
            manifest = json.loads(package.read(MANIFEST))
            if manifest.get("app_id") != APP_ID:
                raise UpdateError("This ZIP belongs to a different application")
            new_version = manifest.get("version")
            if not isinstance(new_version, str) or _version(new_version) <= _version(current_version):
                raise UpdateError("This update is not newer than the installed version")
            files = manifest.get("files")
            if not isinstance(files, dict) or "version.py" not in files or "app.py" not in files:
                raise UpdateError("Update manifest is incomplete")
            if set(package.namelist()) != set(files) | {MANIFEST}:
                raise UpdateError("Archive files do not match release manifest")
            for name, expected_hash in files.items():
                if not isinstance(name, str) or not _safe_relpath(name):
                    raise UpdateError(f"Unsafe update path: {name}")
                if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                    raise UpdateError(f"Invalid SHA-256 digest for {name}")
                if _sha256(package.read(name)) != expected_hash:
                    raise UpdateError(f"File checksum failed: {name}")
            version_text = package.read("version.py").decode("utf-8")
            if f'APP_VERSION = "{new_version}"' not in version_text:
                raise UpdateError("Version file disagrees with release manifest")
            return manifest
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Invalid update ZIP: {exc}") from exc


def stage_package(root: Path, raw: bytes) -> str:
    manifest = validate_package(raw, installed_version(root))
    directory = root / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="incoming-", suffix=".zip", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        os.replace(tmp_name, directory / PENDING)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return manifest["version"]


def apply_pending(root: Path) -> str | None:
    package_path = root / "updates" / PENDING
    if not package_path.exists():
        return None
    raw = package_path.read_bytes()
    manifest = validate_package(raw, installed_version(root))
    with tempfile.TemporaryDirectory(prefix="update-work-", dir=root / "updates") as td:
        scratch = Path(td)
        unpacked = scratch / "files"
        backup = scratch / "backup"
        unpacked.mkdir()
        backup.mkdir()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for name in manifest["files"]:
                path = unpacked / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(z.read(name))
        modified: list[tuple[Path, Path | None]] = []
        dependency_change = False
        try:
            # Version is installed last. The app will never report a new version
            # until all other files have been copied successfully.
            names = [n for n in manifest["files"] if n != "version.py"] + ["version.py"]
            for name in names:
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                old = backup / name if dest.exists() else None
                if old:
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, old)
                if name == "requirements.txt":
                    dependency_change = not dest.exists() or dest.read_bytes() != (unpacked / name).read_bytes()
                modified.append((dest, old))
                os.replace(unpacked / name, dest)
            if dependency_change:
                (root / "updates" / "needs_dependencies.txt").write_text("Sync dependencies on next launch\n", encoding="utf-8")
        except Exception:
            for dest, old in reversed(modified):
                if old and old.exists():
                    os.replace(old, dest)
                elif not old and dest.exists():
                    dest.unlink()
            raise
    package_path.unlink()
    return manifest["version"]


def remote_update_source(root: Path) -> str | None:
    config = root / "update-source.json"
    if not config.exists():
        return None
    value = json.loads(config.read_text(encoding="utf-8")).get("manifest_url")
    if value is None:
        return None
    if not isinstance(value, str) or urllib.parse.urlsplit(value).scheme != "https":
        raise UpdateError("Remote update feed must use HTTPS")
    return value


def _get_url(url: str, limit: int, expected_host: str | None = None) -> bytes:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UpdateError("Update URLs must use HTTPS")
    if expected_host and parts.hostname.casefold() != expected_host.casefold():
        raise UpdateError("Release archive is not on the trusted update host")
    request = urllib.request.Request(url, headers={"User-Agent": "VibeFinderUpdater/0.3"})
    with urllib.request.urlopen(request, timeout=15) as response:
        final_parts = urllib.parse.urlsplit(response.geturl())
        if final_parts.scheme != "https" or (expected_host and final_parts.hostname != expected_host):
            raise UpdateError("Untrusted update redirect")
        result = response.read(limit + 1)
        if len(result) > limit:
            raise UpdateError("Update download exceeded size limit")
        return result


def check_online(root: Path) -> dict | None:
    url = remote_update_source(root)
    if url is None:
        return None
    host = urllib.parse.urlsplit(url).hostname
    remote = json.loads(_get_url(url, 64 * 1024, host))
    if remote.get("app_id") != APP_ID or not isinstance(remote.get("version"), str):
        raise UpdateError("Update feed does not describe Vibe Finder")
    _version(remote["version"])
    if _version(remote["version"]) <= _version(installed_version(root)):
        return {"current": True, "version": installed_version(root)}
    archive_url = remote.get("archive_url")
    sha = remote.get("sha256")
    if not isinstance(archive_url, str) or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise UpdateError("Update feed has no valid release URL and checksum")
    if urllib.parse.urlsplit(archive_url).hostname != host:
        raise UpdateError("Update ZIP and manifest must use same trusted host")
    return {"current": False, "version": remote["version"], "archive_url": archive_url, "sha256": sha, "host": host}


def stage_online(root: Path, release: dict) -> str:
    if release.get("current") or not release.get("archive_url"):
        raise UpdateError("No newer online release selected")
    raw = _get_url(release["archive_url"], MAX_ARCHIVE_BYTES, release["host"])
    if _sha256(raw) != release["sha256"]:
        raise UpdateError("Release download checksum failed")
    return stage_package(root, raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Vibe Finder updater")
    parser.add_argument("--apply-pending", action="store_true")
    args = parser.parse_args()
    if args.apply_pending:
        try:
            version = apply_pending(Path(__file__).resolve().parent)
            if version:
                print(f"Vibe Finder updated successfully to {version}.")
            return 0
        except Exception as exc:
            print(f"Could not apply update: {exc}")
            print("Your current installation has been kept. Please remove the bad update from the updates folder.")
            return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
