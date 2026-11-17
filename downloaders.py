"""Allowed Creative Commons audio download routing. No ripping or hidden streams."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin
import requests

from jamendo import temporary_audio as jamendo_audio
from catalogues import trusted_audio_url

MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_REDIRECTS = 3


class AudioDownloadError(ValueError):
    pass


def download_track_audio(track: dict, dest: Path, *, session=requests) -> Path:
    """Download a *declared eligible* audio file with a strict source host policy."""
    source = track.get("source", "Jamendo")
    if source == "Jamendo":
        return jamendo_audio(track, dest, session=session)
    url = track.get("audio_url")
    if source not in ("ccMixter", "Wikimedia Commons") or not trusted_audio_url(url, source):
        raise AudioDownloadError("Untrusted catalogue or audio URL")
    dest = Path(dest)
    total = 0
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not trusted_audio_url(url, source):
                raise AudioDownloadError("Untrusted download redirect")
            with session.get(url, stream=True, timeout=(7, 25), allow_redirects=False,
                             headers={"User-Agent": "VibeFinder/0.6 (noncommercial audio comparison)"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    nxt = urljoin(url, response.headers.get("Location") or "")
                    if not trusted_audio_url(nxt, source):
                        raise AudioDownloadError("Redirect to untrusted audio host or file")
                    url = nxt
                    continue
                response.raise_for_status()
                if not trusted_audio_url(response.url, source):
                    raise AudioDownloadError("Audio returned from untrusted host")
                length = response.headers.get("Content-Length", "")
                if str(length).isdigit() and int(length) > MAX_AUDIO_BYTES:
                    raise AudioDownloadError("Audio exceeds the size limit")
                with dest.open("wb") as output:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_AUDIO_BYTES:
                            raise AudioDownloadError("Audio exceeds the size limit")
                        output.write(chunk)
                if total < 512:
                    raise AudioDownloadError("Audio download was empty or invalid")
                return dest
        raise AudioDownloadError("Too many audio redirects")
    except (requests.RequestException, OSError, AudioDownloadError, ValueError) as exc:
        dest.unlink(missing_ok=True)
        if isinstance(exc, AudioDownloadError):
            raise
        raise AudioDownloadError(f"Audio download failed: {exc}") from exc
