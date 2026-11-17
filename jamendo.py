"""Conservative, ephemeral Jamendo candidate collection for acoustic comparison.

Only tracks with audiodownload_allowed=true and a download URL are analysed.
No audio is persisted; callers put temporary downloaded files into a temporary
folder and delete them after feature extraction. All results are heuristic.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import os
import re
from pathlib import Path
from typing import Mapping
from urllib.parse import urljoin, urlsplit

import requests

API_URL = "https://api.jamendo.com/v3.0/tracks/"
# Public Jamendo application identifier, NOT a password or private API secret.
# It makes the bundled catalogue integration work without any user setup.
DEFAULT_CLIENT_ID = "fdfc0cdf"
MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_SEARCH_RESULTS = 32
MAX_REDIRECTS = 3
SEARCH_TAGS = ("electronic", "hiphop", "rock", "ambient", "metal", "jazz", "dance", "instrumental")


class JamendoError(ValueError):
    pass


def valid_client_id(value: str) -> bool:
    # Jamendo client ids are typically eight hexadecimal digits. Allow for
    # possible future alphanumeric IDs; reject punctuation and URL inputs.
    return bool(re.fullmatch(r"[A-Za-z0-9]{8,64}", value.strip()))



def resolve_client_id(settings_file: Path, *, environment: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Choose a usable public application ID without requiring user input.

    Priority: valid environment override, valid locally saved override, built-in
    public ID. Bad/corrupt local settings cannot make discovery unusable.
    """
    source = os.environ if environment is None else environment
    configured = str(source.get("JAMENDO_CLIENT_ID", "")).strip()
    if valid_client_id(configured):
        return configured, "environment override"

    try:
        data = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            saved = str(data.get("client_id", "")).strip()
            if valid_client_id(saved):
                return saved, "saved override"
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_CLIENT_ID, "built-in default"

def _api_json(client_id: str, *, tag: str, limit: int, session=requests):
    if not valid_client_id(client_id):
        raise JamendoError("Please enter a valid Jamendo application Client ID.")
    try:
        response = session.get(API_URL, params={
            "client_id": client_id.strip(), "format": "json",
            "limit": min(200, max(1, limit)), "tags": tag,
            "include": "licenses+musicinfo", "type": "single albumtrack",
            "audioformat": "mp32", "audiodlformat": "mp32",
        }, timeout=(6, 15))
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise JamendoError(f"Jamendo catalogue request failed: {exc}") from exc
    status = payload.get("headers", {})
    if status.get("status") != "success":
        raise JamendoError("Jamendo rejected the request: " + str(status.get("error_message") or status.get("code") or "unknown API error"))
    return payload.get("results", [])


def find_candidates(client_id: str, max_tracks: int = 24, *, session=requests,
                    tags: tuple[str, ...] = SEARCH_TAGS,
                    exclude_ids: set[str] | None = None) -> tuple[list[dict], list[str]]:
    """Collect a fair cross-style sample of *download-permitted* tracks.

    Round-robin selection prevents the first API tags from monopolising the
    acoustic scan. A second pass can exclude IDs already analysed. This is
    bounded discovery, not catalogue-wide ranking or a claim of AI matching.
    """
    if max_tracks < 1 or not tags:
        return [], []
    seen = set(exclude_ids or ())
    by_style: dict[str, list[dict]] = {}
    warnings: list[str] = []
    unique_tags = tuple(dict.fromkeys(tags))
    per_style = min(50, max(4, ((max_tracks + len(unique_tags) - 1) // len(unique_tags)) * 3))
    # Independent metadata queries can overlap, while results remain ordered
    # by the user's tag list and concurrent API calls remain tightly bounded.
    with ThreadPoolExecutor(max_workers=min(4, len(unique_tags))) as pool:
        requests_by_tag = {
            tag: pool.submit(_api_json, client_id, tag=tag, limit=per_style, session=session)
            for tag in unique_tags
        }
        responses_by_tag = {}
        for tag in unique_tags:
            try:
                responses_by_tag[tag] = requests_by_tag[tag].result()
            except JamendoError as exc:
                warnings.append(f"{tag}: {exc}")
                responses_by_tag[tag] = []
    for tag in unique_tags:
        rows = responses_by_tag[tag]
        eligible = []
        for item in rows:
            if not item.get("audiodownload_allowed") or not item.get("audiodownload"):
                continue
            url = item["audiodownload"]
            if not _is_trusted_audio_url(url):
                continue
            identifier = str(item.get("id", ""))
            if not identifier or identifier in seen:
                continue
            license_url = item.get("license_ccurl") or ""
            if not license_url and isinstance(item.get("license"), dict):
                license_url = item["license"].get("ccurl", "")
            eligible.append({
                "id": identifier, "artist": str(item.get("artist_name", "Unknown artist")),
                "title": str(item.get("name", "Untitled")), "audio_url": url,
                "listen_url": str(item.get("shareurl", "")) or f"https://www.jamendo.com/track/{identifier}",
                "license_url": str(license_url), "source_style": tag,
            })
        by_style[tag] = eligible
    selected = []
    cursor = {tag: 0 for tag in unique_tags}
    while len(selected) < max_tracks:
        advanced = False
        for tag in unique_tags:
            rows = by_style.get(tag, [])
            while cursor[tag] < len(rows):
                track = rows[cursor[tag]]
                cursor[tag] += 1
                if track["id"] in seen:
                    continue
                selected.append(track)
                seen.add(track["id"])
                advanced = True
                break
            if len(selected) >= max_tracks:
                break
        if not advanced:
            break
    return selected, warnings


def _is_trusted_audio_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname is not None
                and (parsed.hostname == "jamendo.com" or parsed.hostname.endswith(".jamendo.com"))
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443))
    except (ValueError, TypeError):
        return False


def test_connection(client_id: str, *, session=requests) -> dict:
    """Make one metadata-only API request; never download audio for diagnostics."""
    rows = _api_json(client_id, tag="electronic", limit=5, session=session)
    downloadable = sum(
        bool(row.get("audiodownload_allowed") and row.get("audiodownload")
             and _is_trusted_audio_url(row["audiodownload"]))
        for row in rows
    )
    return {"returned": len(rows), "downloadable": downloadable}


def temporary_audio(track: dict, dest: Path, *, session=requests) -> Path:
    """Stream eligible audio to a temporary file; validate *each* redirect first."""
    url = track.get("audio_url")
    if not isinstance(url, str) or not _is_trusted_audio_url(url):
        raise JamendoError("Track download host was not trusted.")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        for hop in range(MAX_REDIRECTS + 1):
            if not _is_trusted_audio_url(url):
                raise JamendoError("Track download redirected to an untrusted host.")
            # Never automatically follow redirects: reject untrusted targets before
            # making an HTTP request to them.
            with session.get(url, stream=True, timeout=(6, 25), allow_redirects=False) as response:
                code = response.status_code
                if code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "")
                    if not location:
                        raise JamendoError("Audio redirect had no destination.")
                    next_url = urljoin(url, location)
                    if not _is_trusted_audio_url(next_url):
                        raise JamendoError("Audio redirected to an untrusted host.")
                    url = next_url
                    continue
                response.raise_for_status()
                if not _is_trusted_audio_url(response.url):
                    raise JamendoError("Audio response came from an untrusted host.")
                announced = response.headers.get("Content-Length")
                if announced and announced.isdecimal() and int(announced) > MAX_AUDIO_BYTES:
                    raise JamendoError("Track exceeds the analysis download limit.")
                with dest.open("wb") as output:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_AUDIO_BYTES:
                            raise JamendoError("Track exceeds the analysis download limit.")
                        output.write(chunk)
                if total < 512:
                    raise JamendoError("Downloaded audio was empty or invalid.")
                return dest
        raise JamendoError("Too many audio redirects.")
    except JamendoError:
        dest.unlink(missing_ok=True)
        raise
    except (requests.RequestException, OSError, ValueError) as exc:
        dest.unlink(missing_ok=True)
        raise JamendoError(f"Track download failed: {exc}") from exc
