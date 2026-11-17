"""Conservative, ephemeral Jamendo candidate collection for acoustic comparison.

Only tracks with audiodownload_allowed=true and a download URL are analysed.
No audio is persisted; callers put temporary downloaded files into a temporary
folder and delete them after feature extraction. All results are heuristic.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

API_URL = "https://api.jamendo.com/v3.0/tracks/"
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
                    tags: tuple[str, ...] = SEARCH_TAGS) -> tuple[list[dict], list[str]]:
    """Sample multiple styles; rank *only later* using acoustic descriptors.

    The returned list is not a search of all Jamendo tracks. All tracks with
    forbidden downloads are discarded, even if their streaming URL exists.
    """
    if max_tracks < 1:
        return [], []
    chosen: dict[str, dict] = {}
    warnings: list[str] = []
    per_style = max(3, (max_tracks + max(len(tags), 1) - 1) // max(len(tags), 1) * 2)
    for tag in tags:
        try:
            rows = _api_json(client_id, tag=tag, limit=per_style, session=session)
        except JamendoError as exc:
            warnings.append(f"{tag}: {exc}")
            continue
        for item in rows:
            if not item.get("audiodownload_allowed") or not item.get("audiodownload"):
                continue
            url = item["audiodownload"]
            if not _is_trusted_audio_url(url):
                continue
            identifier = str(item.get("id", ""))
            if not identifier or identifier in chosen:
                continue
            license_url = item.get("license_ccurl") or ""
            if not license_url and isinstance(item.get("license"), dict):
                license_url = item["license"].get("ccurl", "")
            chosen[identifier] = {
                "id": identifier, "artist": str(item.get("artist_name", "Unknown artist")),
                "title": str(item.get("name", "Untitled")), "audio_url": url,
                "listen_url": str(item.get("shareurl", "")) or f"https://www.jamendo.com/track/{identifier}",
                "license_url": str(license_url), "source_style": tag,
            }
            if len(chosen) >= max_tracks:
                return list(chosen.values()), warnings
    return list(chosen.values()), warnings


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
