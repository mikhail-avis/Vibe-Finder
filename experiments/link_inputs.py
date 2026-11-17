"""Browser-music links and opt-in Windows playback captures for the audio lab.

No site audio is downloaded or extracted. Music-site URLs are opened in the
user's browser, and the user explicitly records their own selected Windows
playback output using WASAPI loopback. Only the WAV bytes are compared.
"""
from __future__ import annotations

import ipaddress
import time
from urllib.parse import urlsplit, urlunsplit

from live_audio import capture_output

MAX_LINK_CHARS = 2048
MAX_CAPTURE_BYTES = 48 * 1024 * 1024
CAPTURE_DURATIONS = (10, 20, 30)


def inspect_music_link(value: str) -> dict:
    """Check a browser-only HTTPS link without making a network request.

    Returns a display label and normalized URL. We deliberately do not fetch,
    scrape, download or otherwise attempt to retrieve music from this URL.
    """
    if not isinstance(value, str) or len(value) > MAX_LINK_CHARS:
        raise ValueError("Paste a music-page URL of at most 2,048 characters.")
    url = value.strip()
    try:
        bits = urlsplit(url)
        host = (bits.hostname or "").lower().rstrip(".")
        port = bits.port
    except ValueError as exc:
        raise ValueError("Enter a valid HTTPS music link.") from exc
    if (bits.scheme.lower() != "https" or not host or bits.username is not None
            or bits.password is not None or port not in (None, 443)
            or any(ch.isspace() or ord(ch) < 32 for ch in url)):
        raise ValueError("Enter a public HTTPS music link (not a file, local address or password-bearing URL).")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise ValueError("Local or private network links are not supported.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Please use a public music website name, not an IP address.")
    if host.startswith("[") or any(c in host for c in "\\/ %"):
        raise ValueError("Invalid music website hostname.")

    def matches(site: str) -> bool:
        return host == site or host.endswith("." + site)

    if matches("youtube.com") or host in ("youtu.be", "www.youtu.be"):
        provider = "YouTube"
    elif matches("soundcloud.com"):
        provider = "SoundCloud"
    elif matches("spotify.com"):
        provider = "Spotify"
    elif matches("bandcamp.com") or host.endswith(".bandcamp.com"):
        provider = "Bandcamp"
    elif matches("jamendo.com"):
        provider = "Jamendo"
    elif matches("mixter.plus") or matches("ccmixter.org"):
        provider = "ccMixter"
    elif matches("wikimedia.org") or matches("wikipedia.org"):
        provider = "Wikimedia"
    else:
        provider = "Music website"
    canonical = urlunsplit(("https", bits.netloc.lower(), bits.path or "/", bits.query, bits.fragment))
    return {"url": canonical, "provider": provider}


def capture_link_audio(link: str, device_index: int | None, seconds: int = 20, *, recorder=None) -> dict:
    """Capture explicit, selected playback for a pasted link. Never download it."""
    info = inspect_music_link(link)
    if device_index is None or not isinstance(device_index, int) or device_index < 0:
        raise ValueError("Select a Windows speaker or headphones output first.")
    if seconds not in CAPTURE_DURATIONS:
        raise ValueError("Choose a 10, 20 or 30 second recording.")
    start = time.perf_counter()
    raw = (recorder or capture_output)(seconds, device_index=device_index)
    if not isinstance(raw, bytes) or not raw.startswith(b"RIFF") or raw[8:12] != b"WAVE":
        raise ValueError("The playback device did not return a valid WAV recording.")
    if len(raw) > MAX_CAPTURE_BYTES:
        raise ValueError("Playback recording exceeded the temporary 48 MB limit.")
    return {"url": info["url"], "provider": info["provider"], "device_index": device_index,
            "duration_seconds": seconds, "elapsed_seconds": round(time.perf_counter() - start, 2),
            "wav": raw}


def capture_is_current(captured: dict | None, link: str) -> bool:
    if not captured:
        return False
    try:
        return inspect_music_link(link)["url"] == captured.get("url") and bool(captured.get("wav"))
    except ValueError:
        return False
