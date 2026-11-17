"""Additional Creative Commons audio catalogues for genuine acoustic discovery.

No metadata-only matches: returned candidates must pass an eligible downloadable
file check, then the shared DSP pipeline scores downloaded waveform data.

The external APIs are intentionally best-effort. An unavailable provider
shouldn't prevent searches in other enabled catalogues.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from html import unescape
import re
from urllib.parse import quote, urlsplit

import requests

from jamendo import SEARCH_TAGS, find_candidates as find_jamendo

SOURCES = ("Jamendo", "ccMixter", "Wikimedia Commons")
CCMIXTER_API = "https://ccmixter.org/api/query"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
CCMIXTER_AUDIO_HOSTS = ("ccmixter.org",)
COMMONS_AUDIO_HOSTS = ("upload.wikimedia.org",)
AUDIO_EXTENSIONS = frozenset({".mp3", ".ogg", ".oga", ".flac", ".wav", ".m4a", ".opus"})
MAX_CANDIDATES = 60


class CatalogueError(ValueError):
    pass


def trusted_audio_url(url: str, source: str) -> bool:
    """Reject untrusted hosts/unsafe schemes before downloading or redirecting."""
    allowed = {"ccMixter": CCMIXTER_AUDIO_HOSTS, "Wikimedia Commons": COMMONS_AUDIO_HOSTS}
    if source not in allowed or not isinstance(url, str):
        return False
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.hostname is not None and
                any(p.hostname == host or p.hostname.endswith("." + host) for host in allowed[source]) and
                p.username is None and p.password is None and p.port in (None, 443) and
                _suffix(url) in AUDIO_EXTENSIONS)
    except (ValueError, TypeError):
        return False


def _suffix(url: str) -> str:
    path = urlsplit(url).path.lower()
    return "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""


def _plain(value: object) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", "", str(value or "")))).strip()


def _cc_license(url: str) -> bool:
    """Only direct Creative Commons deeds or CC0/public-domain declarations."""
    if not isinstance(url, str):
        return False
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        path = p.path.lower()
        return (p.scheme in ("http", "https") and host in ("creativecommons.org", "www.creativecommons.org") and
                (path.startswith("/licenses/") or path.startswith("/publicdomain/")))
    except (ValueError, TypeError):
        return False


def _rows(payload):
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("results", "items", "data", "uploads"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
        if isinstance(payload.get("result"), list):
            return [r for r in payload["result"] if isinstance(r, dict)]
    return []


def _get_json(url: str, params: dict, *, session=requests) -> object:
    try:
        response = session.get(url, params=params, timeout=(7, 15), headers={"User-Agent": "VibeFinder/0.6 (audio discovery; info via repository)"})
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise CatalogueError(f"Catalogue API unavailable: {exc}") from exc


def _cc_download(row: dict):
    """Choose a full audio recording, not a thumbnail, player or search URL."""
    files = row.get("files") or row.get("file") or row.get("download_files")
    if isinstance(files, dict):
        files = list(files.values())
    if not isinstance(files, list):
        files = []
    candidates = [f for f in files if isinstance(f, dict)] + [row]
    for item in candidates:
        url = next((item.get(key) for key in ("download_url", "file_download_url", "file_url", "url")
                    if isinstance(item.get(key), str) and item.get(key)), "")
        if trusted_audio_url(url, "ccMixter"):
            return url
    return ""


def _cc_record(row: dict, tag: str) -> dict | None:
    identifier = str(row.get("upload_id") or row.get("id") or "").strip()
    license_url = str(row.get("license_url") or row.get("upload_license_url") or "")
    if not identifier or not _cc_license(license_url):
        return None
    url = _cc_download(row)
    if not url:
        return None
    artist = _plain(row.get("user_real_name") or row.get("user_name") or row.get("artist") or "Unknown artist")
    title = _plain(row.get("upload_name") or row.get("title") or row.get("name") or "Untitled")
    # A published upload page is the main attribution and license-detail link.
    username = str(row.get("user_name") or "").strip()
    link = str(row.get("upload_url") or row.get("page_url") or "")
    if not link.startswith("https://ccmixter.org/"):
        link = f"https://ccmixter.org/files/{quote(username, safe='')}/{identifier}" if username else "https://ccmixter.org/"
    return {"source": "ccMixter", "id": f"ccm:{identifier}", "artist": artist[:150], "title": title[:150],
            "audio_url": url, "listen_url": link, "license_url": license_url,
            "source_style": tag}


def find_ccmixter(max_tracks: int, *, tags: tuple[str, ...] = SEARCH_TAGS,
                   exclude_ids: set[str] | None = None, session=requests) -> tuple[list[dict], list[str]]:
    """ccMixter official Query API; metadata that lacks downloadable audio is discarded."""
    max_tracks = min(max(0, max_tracks), MAX_CANDIDATES)
    if not max_tracks:
        return [], []
    tags = tuple(dict.fromkeys(tags))[:3]
    seen = set(exclude_ids or ())
    chosen, warnings = [], []
    for tag in tags:
        if len(chosen) >= max_tracks:
            break
        try:
            payload = _get_json(CCMIXTER_API, {"f": "json", "dataview": "info", "tags": tag,
                                               "limit": min(18, max(5, max_tracks * 2))}, session=session)
            entries = _rows(payload)
            # Some versions of ccHost return only file rows with dataview=files.
            # Fetch only that alternative when the primary response is not usable.
            if not any(_cc_download(r) for r in entries):
                alternate = _get_json(CCMIXTER_API, {"f": "json", "dataview": "files",
                                                        "tags": tag, "limit": min(18, max(5, max_tracks * 2))}, session=session)
                entries.extend(_rows(alternate))
        except CatalogueError as exc:
            warnings.append(f"ccMixter {tag}: {exc}")
            continue
        for row in entries:
            record = _cc_record(row, tag)
            if record and record["id"] not in seen:
                chosen.append(record)
                seen.add(record["id"])
                if len(chosen) >= max_tracks:
                    break
    return chosen, warnings


def _commons_license(extmetadata: dict) -> str:
    if not isinstance(extmetadata, dict):
        return ""
    short = _plain((extmetadata.get("LicenseShortName") or {}).get("value", "")).lower()
    license_url = str((extmetadata.get("LicenseUrl") or {}).get("value") or "")
    if _cc_license(license_url) and ("cc" in short or "public domain" in short or short.startswith("pd-")):
        return license_url
    # Public domain statements on Commons don't always have a LicenseUrl.
    if short in ("public domain", "cc0", "cc-zero"):
        return "https://creativecommons.org/publicdomain/mark/1.0/"
    return ""


def _commons_record(page: dict, tag: str) -> dict | None:
    if page.get("ns") != 6 or not isinstance(page.get("imageinfo"), list) or not page["imageinfo"]:
        return None
    info = page["imageinfo"][0]
    if not isinstance(info, dict):
        return None
    if info.get("mediatype") not in (None, "AUDIO"):
        return None
    mime = str(info.get("mime") or "")
    if mime and not (mime.startswith("audio/") or mime in ("application/ogg",)):
        return None
    url = info.get("url")
    if not trusted_audio_url(url, "Wikimedia Commons"):
        return None
    if isinstance(info.get("size"), int) and info["size"] > 16 * 1024 * 1024:
        return None
    ext = info.get("extmetadata") or {}
    licence = _commons_license(ext)
    if not licence:
        return None
    title = _plain((ext.get("ObjectName") or {}).get("value") or page.get("title", ""))
    title = re.sub(r"^File:\s*", "", title)
    artist = _plain((ext.get("Artist") or {}).get("value")) or "Commons contributor"
    link = info.get("descriptionurl")
    if not isinstance(link, str) or not link.startswith("https://commons.wikimedia.org/wiki/File:"):
        link = "https://commons.wikimedia.org/wiki/" + quote(str(page.get("title", "")), safe=":()_,.-")
    return {"source": "Wikimedia Commons", "id": f"commons:{page['pageid']}",
            "artist": artist[:150], "title": title[:150], "audio_url": url,
            "listen_url": link, "license_url": licence, "source_style": tag}


def find_commons(max_tracks: int, *, tags: tuple[str, ...] = SEARCH_TAGS,
                 exclude_ids: set[str] | None = None, session=requests) -> tuple[list[dict], list[str]]:
    """Search free-licensed audio *files* on Wikimedia Commons, not webpages."""
    max_tracks = min(max(0, max_tracks), MAX_CANDIDATES)
    if not max_tracks:
        return [], []
    seen = set(exclude_ids or ())
    chosen, warnings = [], []
    for tag in tuple(dict.fromkeys(tags))[:3]:
        try:
            payload = _get_json(COMMONS_API, {
                "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                "gsrsearch": f"filetype:audio music {tag}", "gsrlimit": min(20, max(8, max_tracks * 2)),
                "prop": "imageinfo", "iiprop": "url|mime|mediatype|size|extmetadata",
                "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ObjectName"
            }, session=session)
            pages = (payload.get("query") or {}).get("pages") or {}
            if not isinstance(pages, dict):
                raise CatalogueError("Unexpected Wikimedia API response")
        except (CatalogueError, AttributeError, ValueError) as exc:
            warnings.append(f"Wikimedia Commons {tag}: {exc}")
            continue
        for page in sorted(pages.values(), key=lambda x: x.get("index", 999) if isinstance(x, dict) else 999):
            if not isinstance(page, dict):
                continue
            record = _commons_record(page, tag)
            if record and record["id"] not in seen:
                chosen.append(record)
                seen.add(record["id"])
                if len(chosen) >= max_tracks:
                    return chosen, warnings
    return chosen, warnings


def discover_across_sources(client_id: str, max_tracks: int, *,
                            sources: tuple[str, ...] = SOURCES,
                            tags: tuple[str, ...] = SEARCH_TAGS,
                            exclude_ids: set[str] | None = None,
                            session=requests) -> tuple[list[dict], list[str]]:
    """Bounded fair multi-catalogue discovery. No candidate earns a match without DSP.

    Network calls for different providers overlap. A failed provider contributes
    zero candidates plus warnings, never fabricated or metadata-scored tracks.
    """
    if max_tracks < 1:
        return [], []
    if not 1 <= max_tracks <= MAX_CANDIDATES:
        raise ValueError("Track budget out of range")
    names = tuple(k for k in SOURCES if k in sources)
    if not names:
        return [], ["Select at least one audio catalogue"]
    per = (max_tracks + len(names) - 1) // len(names)
    def invoke(name, requested, excluded):
        if name == "Jamendo":
            candidates, warnings = find_jamendo(client_id, max_tracks=requested, tags=tags,
                                                 exclude_ids=excluded, session=session)
            return [{**item, "source": "Jamendo"} for item in candidates], warnings
        if name == "ccMixter":
            return find_ccmixter(requested, tags=tags, exclude_ids=excluded, session=session)
        return find_commons(requested, tags=tags, exclude_ids=excluded, session=session)
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {name: pool.submit(invoke, name, per, exclude_ids) for name in names}
        batches, warnings = {}, []
        for name in names:
            try:
                batches[name], issues = futures[name].result()
                warnings.extend(issues)
            except Exception as exc:
                batches[name] = []
                warnings.append(f"{name}: {type(exc).__name__}: {exc}")
    selected, seen = [], set(exclude_ids or ())
    for i in range(per):
        for name in names:
            tracks = batches[name]
            if i < len(tracks):
                track = tracks[i]
                identity = (track.get("source"), track.get("id"))
                if identity not in seen:
                    selected.append(track)
                    seen.add(identity)
                if len(selected) >= max_tracks:
                    return selected, warnings
    # If an external provider is empty/unavailable, don't waste the user's
    # entire budget: one bounded extra query to catalogues that had eligible
    # tracks in their first batch. Results still require real DSP analysis.
    if len(selected) < max_tracks:
        available = sorted((n for n in names if len(batches[n]) >= per),
                           key=lambda n: -len(batches[n]))
        for name in available:
            try:
                additional, issues = invoke(name, max_tracks - len(selected),
                                            {item.get("id") for item in selected})
                warnings.extend(issues)
            except Exception as exc:
                warnings.append(f"{name}: backfill failed: {type(exc).__name__}: {exc}")
                continue
            for track in additional:
                identity = (track.get("source"), track.get("id"))
                if identity not in seen:
                    selected.append(track)
                    seen.add(identity)
                if len(selected) >= max_tracks:
                    break
            if len(selected) >= max_tracks:
                break
    return selected, warnings
