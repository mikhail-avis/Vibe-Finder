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
import secrets
import shutil
import stat
import tempfile
import urllib.parse
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

APP_ID = "vibe-finder"
MANIFEST = "release-manifest.json"
PENDING = "pending_update.zip"
EXPERIMENT_PENDING = "pending_experimental.zip"
EXPERIMENT_FOLDER = "experimental-build"
EXPERIMENT_PREVIOUS = "experimental-previous"
ROLLBACK_ARCHIVE = "rollback.zip"
ROLLBACK_PENDING = "pending_rollback.json"
EXPERIMENT_ROLLBACK_PENDING = "pending_experimental_rollback.json"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 120 * 1024 * 1024
MAX_FILES = 250
PROTECTED = {"release-manifest.json", "update-source.json", "run-windows.bat"}
PROTECTED_FOLDERS = {"library", ".venv", "updates", "user-data", "__pycache__", ".git", EXPERIMENT_FOLDER, EXPERIMENT_PREVIOUS}
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


def validate_package(raw: bytes, current_version: str, *, channel: str = "stable") -> dict:
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
            if channel not in ("stable", "experimental"):
                raise UpdateError("Unrecognised release channel")
            declared_channel = manifest.get("channel", "stable")
            if declared_channel != channel:
                raise UpdateError(f"This is a {declared_channel} release, not a {channel} release")
            new_version = manifest.get("version")
            if not isinstance(new_version, str) or _version(new_version) <= _version(current_version):
                raise UpdateError("This update is not newer than the installed version")
            files = manifest.get("files")
            if not isinstance(files, dict) or "version.py" not in files or "app.py" not in files or (channel == "experimental" and "experimental_landing.py" not in files):
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
    if (root / "updates" / ROLLBACK_PENDING).exists():
        raise UpdateError("A rollback is already staged. Restart Vibe Finder before another update.")
    manifest = validate_package(raw, installed_version(root), channel="stable")
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
    if (root / "updates" / ROLLBACK_PENDING).exists():
        raise UpdateError("An update and rollback are both pending. Cancel one before restarting.")
    raw = package_path.read_bytes()
    before_version = installed_version(root)
    manifest = validate_package(raw, before_version, channel="stable")
    snapshot = _make_rollback_snapshot(root, manifest, before_version)
    candidate = root / "updates" / "rollback_candidate.zip"
    _atomic_bytes(candidate, snapshot)
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
            # The rollback copy must become durable before we declare success.
            os.replace(candidate, root / "updates" / ROLLBACK_ARCHIVE)
        except Exception:
            for dest, old in reversed(modified):
                if old and old.exists():
                    os.replace(old, dest)
                elif not old and dest.exists():
                    dest.unlink()
            candidate.unlink(missing_ok=True)
            raise
    package_path.unlink()
    return manifest["version"]


def remote_update_source(root: Path, *, channel: str = "stable") -> str | None:
    config = root / "update-source.json"
    if not config.exists():
        return None
    value = json.loads(config.read_text(encoding="utf-8")).get("manifest_url")
    if value is None:
        return None
    if not isinstance(value, str) or urllib.parse.urlsplit(value).scheme != "https":
        raise UpdateError("Remote update feed must use HTTPS")
    if channel == "stable":
        return value
    if channel != "experimental":
        raise UpdateError("Unrecognised update channel")
    # Derive a sibling, fixed-name experimental feed from the trusted stable feed.
    # This requires no edit to the protected update-source.json in existing installs.
    parts = urllib.parse.urlsplit(value)
    if not parts.path.endswith("/latest.json"):
        raise UpdateError("Stable update feed has no supported experimental sibling")
    return urllib.parse.urlunsplit(parts._replace(
        path=parts.path[:-len("latest.json")] + "experimental/latest.json", query="", fragment=""))


def _get_url(url: str, limit: int, expected_host: str | None = None) -> bytes:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UpdateError("Update URLs must use HTTPS")
    if expected_host and parts.hostname.casefold() != expected_host.casefold():
        raise UpdateError("Release archive is not on the trusted update host")
    request = urllib.request.Request(url, headers={
        "User-Agent": "VibeFinderUpdater/0.4.2",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        final_parts = urllib.parse.urlsplit(response.geturl())
        if final_parts.scheme != "https" or (expected_host and final_parts.hostname != expected_host):
            raise UpdateError("Untrusted update redirect")
        result = response.read(limit + 1)
        if len(result) > limit:
            raise UpdateError("Update download exceeded size limit")
        return result


def check_online(root: Path, *, channel: str = "stable") -> dict | None:
    url = remote_update_source(root, channel=channel)
    if url is None:
        return None
    # GitHub Raw can cache a branch-based latest.json after publication.
    # A fresh per-request query avoids a stale cached manifest. The configured
    # canonical URL and the ZIP host checks remain unchanged.
    parts = urllib.parse.urlsplit(url)
    fresh_query = urllib.parse.urlencode(
        urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        + [("vf_refresh", secrets.token_hex(8))]
    )
    fresh_url = urllib.parse.urlunsplit(parts._replace(query=fresh_query))
    host = parts.hostname
    try:
        remote = json.loads(_get_url(fresh_url, 64 * 1024, host))
    except urllib.error.HTTPError as exc:
        if channel == "experimental" and exc.code == 404:
            return None  # No experimental release published yet.
        raise
    if remote.get("app_id") != APP_ID or not isinstance(remote.get("version"), str):
        raise UpdateError("Update feed does not describe Vibe Finder")
    if channel == "experimental" and remote.get("channel") != "experimental":
        raise UpdateError("Experimental feed must identify its channel")
    if channel == "stable" and remote.get("channel", "stable") != "stable":
        raise UpdateError("Stable feed cannot advertise experimental releases")
    _version(remote["version"])
    local_version = installed_version(root) if channel == "stable" else experimental_version(root)
    if _version(remote["version"]) <= _version(local_version):
        result = {"current": True, "version": local_version, "remote_version": remote["version"]}
        if channel == "experimental":
            result["channel"] = channel
        return result
    archive_url = remote.get("archive_url")
    sha = remote.get("sha256")
    if not isinstance(archive_url, str) or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise UpdateError("Update feed has no valid release URL and checksum")
    if urllib.parse.urlsplit(archive_url).hostname != host:
        raise UpdateError("Update ZIP and manifest must use same trusted host")
    result = {"current": False, "version": remote["version"], "remote_version": remote["version"], "archive_url": archive_url, "sha256": sha, "host": host}
    if channel == "experimental":
        result["channel"] = channel
    return result


def stage_online(root: Path, release: dict, *, channel: str = "stable") -> str:
    if release.get("current") or not release.get("archive_url"):
        raise UpdateError("No newer online release selected")
    raw = _get_url(release["archive_url"], MAX_ARCHIVE_BYTES, release["host"])
    if _sha256(raw) != release["sha256"]:
        raise UpdateError("Release download checksum failed")
    if channel == "stable":
        if release.get("channel", "stable") != "stable":
            raise UpdateError("An experimental release cannot replace your stable installation")
        return stage_package(root, raw)
    if channel == "experimental" and release.get("channel") == "experimental":
        return stage_experimental(root, raw)
    raise UpdateError("Release and update channel do not agree")


# Persistent, exact-file rollback of the last successful *stable* upgrade.
def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="vf-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _make_rollback_snapshot(root: Path, manifest: dict, from_version: str) -> bytes:
    old = {}
    with io.BytesIO() as buf:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in manifest["files"]:
                if not _safe_relpath(name):
                    raise UpdateError("Unsafe rollback target")
                path = root / name
                if path.is_symlink():
                    raise UpdateError("Refusing to back up symbolic-linked application files")
                if path.exists():
                    data = path.read_bytes()
                    old[name] = _sha256(data)
                    z.writestr("previous/" + name, data)
                else:
                    old[name] = None
            metadata = {"app_id": APP_ID, "schema": 1, "from_version": from_version,
                        "to_version": manifest["version"], "previous_files": old,
                        "installed_files": manifest["files"]}
            z.writestr("rollback-metadata.json", json.dumps(metadata, sort_keys=True))
        archive = buf.getvalue()
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise UpdateError("Rollback snapshot exceeds size limit")
    return archive


def _verify_rollback(root: Path, *, check_current: bool = True) -> tuple[dict, bytes]:
    path = root / "updates" / ROLLBACK_ARCHIVE
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise UpdateError("No valid rollback backup is available")
    raw = path.read_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if len(names) > MAX_FILES + 1 or len(names) != len(set(names)):
                raise UpdateError("Invalid rollback archive entries")
            meta = json.loads(z.read("rollback-metadata.json"))
            if meta.get("app_id") != APP_ID or meta.get("schema") != 1:
                raise UpdateError("Unexpected rollback metadata")
            _version(meta["from_version"])
            _version(meta["to_version"])
            old = meta["previous_files"]
            installed = meta["installed_files"]
            if not isinstance(old, dict) or not isinstance(installed, dict) or set(old) != set(installed):
                raise UpdateError("Rollback file lists disagree")
            if any(not _safe_relpath(name) for name in old):
                raise UpdateError("Unsafe rollback file path")
            expected_names = {"rollback-metadata.json"} | {"previous/" + name for name, sha in old.items() if sha is not None}
            if set(names) != expected_names:
                raise UpdateError("Rollback archive files disagree with metadata")
            for name, expected in old.items():
                if expected is None:
                    continue
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or _sha256(z.read("previous/" + name)) != expected:
                    raise UpdateError("Rollback backup checksum failed")
            if check_current:
                if installed_version(root) != meta["to_version"]:
                    raise UpdateError("Rollback is for a different installed version")
                for name, expected in installed.items():
                    target = root / name
                    if target.is_symlink() or not target.is_file() or _sha256(target.read_bytes()) != expected:
                        raise UpdateError(f"Application file {name} changed after the update; rollback blocked")
            return meta, raw
    except (zipfile.BadZipFile, UnicodeError, KeyError, ValueError, TypeError) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"Invalid rollback archive: {exc}") from exc


def rollback_status(root: Path) -> dict | None:
    try:
        meta, _ = _verify_rollback(root)
        return {"installed": meta["to_version"], "restore": meta["from_version"]}
    except (UpdateError, OSError):
        return None


def stage_rollback(root: Path) -> str:
    if (root / "updates" / PENDING).exists():
        raise UpdateError("Finish or remove the pending stable update before staging a rollback")
    meta, raw = _verify_rollback(root)
    _atomic_bytes(root / "updates" / ROLLBACK_PENDING, json.dumps({
        "sha256": _sha256(raw), "to_version": meta["to_version"]}).encode("utf-8"))
    return meta["from_version"]


def apply_pending_rollback(root: Path) -> str | None:
    pending = root / "updates" / ROLLBACK_PENDING
    if not pending.exists():
        return None
    if (root / "updates" / PENDING).exists():
        raise UpdateError("Stable update and rollback cannot be pending simultaneously")
    meta, raw = _verify_rollback(root)
    marker = json.loads(pending.read_text(encoding="utf8"))
    if marker.get("sha256") != _sha256(raw) or marker.get("to_version") != meta["to_version"]:
        raise UpdateError("The staged rollback backup has changed")
    old = meta["previous_files"]
    with tempfile.TemporaryDirectory(dir=root / "updates", prefix="rollback-work-") as temp:
        backup_dir = Path(temp)
        changed = []
        requirements_changed = False
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            try:
                for name in [n for n in old if n != "version.py"] + ["version.py"]:
                    dest = root / name
                    current_backup = backup_dir / name
                    current_backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, current_backup)
                    changed.append((dest, current_backup))
                    if name == "requirements.txt" and old[name] != _sha256(dest.read_bytes()):
                        requirements_changed = True
                    if old[name] is None:
                        dest.unlink(missing_ok=True)
                    else:
                        scratch = backup_dir / ("replacement-" + name.replace("/", "_"))
                        scratch.write_bytes(z.read("previous/" + name))
                        os.replace(scratch, dest)
                if requirements_changed:
                    _atomic_bytes(root / "updates" / "needs_dependencies.txt", b"Sync dependencies on next launch\n")
            except Exception:
                for dest, original in reversed(changed):
                    os.replace(original, dest)
                raise
    pending.unlink()
    (root / "updates" / ROLLBACK_ARCHIVE).unlink()
    return meta["from_version"]


# Experimental builds never write into the stable application directory.
def experimental_version(root: Path) -> str:
    version_file = root / EXPERIMENT_FOLDER / "version.py"
    return installed_version(root / EXPERIMENT_FOLDER) if version_file.is_file() else "0.0"


def stage_experimental(root: Path, raw: bytes) -> str:
    if (root / "updates" / EXPERIMENT_ROLLBACK_PENDING).exists():
        raise UpdateError("An experimental rollback is already pending")
    manifest = validate_package(raw, experimental_version(root), channel="experimental")
    _atomic_bytes(root / "updates" / EXPERIMENT_PENDING, raw)
    return manifest["version"]


def apply_pending_experimental(root: Path) -> str | None:
    staged = root / "updates" / EXPERIMENT_PENDING
    if not staged.exists():
        return None
    raw = staged.read_bytes()
    manifest = validate_package(raw, experimental_version(root), channel="experimental")
    updates = root / "updates"
    target = root / EXPERIMENT_FOLDER
    previous = updates / EXPERIMENT_PREVIOUS
    with tempfile.TemporaryDirectory(prefix="experiment-work-", dir=updates) as td:
        scratch = Path(td) / "incoming"
        scratch.mkdir()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for name in manifest["files"]:
                to = scratch / name
                to.parent.mkdir(parents=True, exist_ok=True)
                to.write_bytes(z.read(name))
        moved_old = False
        try:
            # Renaming can fail on Windows when the experimental process is open.
            # In that case, the stable app is untouched and the package stays staged.
            if target.exists():
                if previous.exists():
                    shutil.rmtree(previous)
                os.replace(target, previous)
                moved_old = True
            os.replace(scratch, target)
        except Exception:
            if moved_old and not target.exists() and previous.exists():
                os.replace(previous, target)
            raise
    staged.unlink()
    return manifest["version"]


def experimental_rollback_status(root: Path) -> str | None:
    previous = root / "updates" / EXPERIMENT_PREVIOUS
    if previous.is_dir() and (previous / "version.py").is_file():
        return installed_version(previous)
    return None


def stage_experimental_rollback(root: Path) -> str:
    previous_version = experimental_rollback_status(root)
    if not previous_version or (root / "updates" / EXPERIMENT_PENDING).exists():
        raise UpdateError("No previous experimental build is available to restore")
    _atomic_bytes(root / "updates" / EXPERIMENT_ROLLBACK_PENDING, previous_version.encode("utf8"))
    return previous_version


def apply_pending_experimental_rollback(root: Path) -> str | None:
    pending = root / "updates" / EXPERIMENT_ROLLBACK_PENDING
    if not pending.exists():
        return None
    previous = root / "updates" / EXPERIMENT_PREVIOUS
    if not previous.exists() or experimental_rollback_status(root) != pending.read_text(encoding="utf8"):
        raise UpdateError("Previous experimental version was removed or changed")
    target = root / EXPERIMENT_FOLDER
    if (root / "updates" / EXPERIMENT_PENDING).exists():
        raise UpdateError("Both an experimental upgrade and rollback are staged")
    # Move current experimental directory out of the way, then restore previous.
    obsolete = root / "updates" / "experimental-discard"
    if obsolete.exists():
        shutil.rmtree(obsolete)
    if target.exists():
        os.replace(target, obsolete)
    try:
        os.replace(previous, target)
    except Exception:
        if obsolete.exists() and not target.exists():
            os.replace(obsolete, target)
        raise
    pending.unlink()
    if obsolete.exists():
        shutil.rmtree(obsolete)
    return installed_version(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Vibe Finder updater")
    parser.add_argument("--apply-pending", action="store_true")
    args = parser.parse_args()
    if args.apply_pending:
        try:
            root = Path(__file__).resolve().parent
            rollback_version = apply_pending_rollback(root)
            if rollback_version:
                print(f"Vibe Finder stable app restored to {rollback_version}.")
            version = apply_pending(root)
            if version:
                print(f"Vibe Finder updated successfully to {version}.")
            # An experimental failure must never prevent the stable application from launching.
            try:
                restored = apply_pending_experimental_rollback(root)
                if restored:
                    print(f"Experimental build restored to {restored}.")
                experiment = apply_pending_experimental(root)
                if experiment:
                    print(f"Experimental build {experiment} installed separately.")
            except Exception as exp_error:
                print(f"WARNING: Experimental installation not applied: {exp_error}")
            return 0
        except Exception as exc:
            print(f"Could not apply update: {exc}")
            print("Your current installation has been kept. Please remove the bad update from the updates folder.")
            return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
