"""Build a separate updater-safe experimental ZIP and opt-in update feed.

Does not publish, install model weights, or alter the stable feed.
Usage: python scripts/build_experimental_release.py --owner USER --repo Vibe-Finder
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_VERSION = "0.7.2"
FILES = (
    "experimental_landing.py", "engine.py", "live_audio.py", "requirements.txt",
    "experiments/link_inputs.py",
    "experiments/__init__.py", "experiments/audio_models.py",
    "experiments/audio_benchmark.py", "experiments/README.md",
)


def build(owner: str, repo: str, root: Path = ROOT):
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", item) for item in (owner, repo)):
        raise ValueError("Invalid GitHub owner/repository")
    sys.path.insert(0, str(root))
    from updater import validate_package
    files = {name: (root / name).read_bytes() for name in FILES}
    # Required by the ZIP validator. The stable application's version is not changed.
    files["version.py"] = f'APP_VERSION = "{EXPERIMENT_VERSION}"\n'.encode()
    files["app.py"] = b'from experimental_landing import main\n\nif __name__ == "__main__": main()\n'
    manifest = {
        "app_id": "vibe-finder", "version": EXPERIMENT_VERSION,
        "channel": "experimental", "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for name, data in files.items():
            zip_file.writestr(name, data)
        zip_file.writestr("release-manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    raw = buffer.getvalue()
    validate_package(raw, "0.0", channel="experimental")
    destination = root / "releases" / "experimental"
    destination.mkdir(parents=True, exist_ok=True)
    package = destination / f"v{EXPERIMENT_VERSION}.zip"
    package.write_bytes(raw)
    base = f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/main/releases/experimental"
    feed = {
        "app_id": "vibe-finder", "channel": "experimental", "version": EXPERIMENT_VERSION,
        "archive_url": f"{base}/{package.name}", "sha256": hashlib.sha256(raw).hexdigest(),
    }
    manifest_path = destination / "latest.json"
    manifest_path.write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8")
    return package, manifest_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", default="Vibe-Finder")
    args = parser.parse_args()
    package, feed = build(args.owner, args.repo)
    print(f"Created experimental package: {package} ({package.stat().st_size:,} bytes)")
    print(f"Experimental feed (publish ZIP first): {feed}")
