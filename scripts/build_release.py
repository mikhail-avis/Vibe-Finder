"""Build GitHub Raw-hosted Vibe Finder update artifacts, no API key needed.

Usage: python scripts/build_release.py --owner YOUR-GITHUB-NAME --repo vibe-finder
Then commit the resulting releases/ files to a PUBLIC repository's main branch.

For a prior installation, bump APP_VERSION before creating
an update: a release with the same version is intentionally not installable.
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
RELEASE_FILES = (
    "app.py", "engine.py", "discovery.py", "jamendo.py", "candidate_pipeline.py",
    "adaptive_discovery.py", "history.py", "catalogues.py", "downloaders.py", "live_audio.py", "updater.py", "version.py",
    "requirements.txt", "README.md",
)


def build(owner: str, repo: str, root: Path = ROOT) -> tuple[Path, Path]:
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", p) for p in (owner, repo)):
        raise ValueError("Invalid GitHub owner or repository name")
    sys.path.insert(0, str(root))
    from updater import installed_version, validate_package

    version = installed_version(root)
    files = {name: (root / name).read_bytes() for name in RELEASE_FILES}
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    manifest = {"app_id": "vibe-finder", "version": version, "files": digests}
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("release-manifest.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    validate_package(raw.getvalue(), "0.0")
    out = root / "releases"
    out.mkdir(exist_ok=True)
    package_path = out / f"v{version}.zip"
    package_path.write_bytes(raw.getvalue())
    # Raw GitHub paths share a host, satisfying existing updater's host policy.
    # A public repo is required so the desktop updater needs no GitHub token.
    base = f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/main/releases"
    feed = {
        "app_id": "vibe-finder",
        "version": version,
        "archive_url": f"{base}/{package_path.name}",
        "sha256": hashlib.sha256(raw.getvalue()).hexdigest(),
    }
    feed_path = out / "latest.json"
    feed_path.write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8")
    print(f"Built {package_path} ({package_path.stat().st_size:,} bytes)")
    print(f"Feed: {feed_path}")
    print("Configure your installed app's update-source.json with this URL:")
    print(base + "/latest.json")
    print("IMPORTANT: Commit the ZIP first, then latest.json, so the feed is never ahead of the archive.")
    return package_path, feed_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", default="vibe-finder")
    args = ap.parse_args()
    build(args.owner, args.repo)
