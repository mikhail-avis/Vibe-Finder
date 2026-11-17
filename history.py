"""Private, local search history. Metadata only; never stores recorded audio."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import tempfile

MAX_RECENT = 30


def _safe_listen_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        url = urlsplit(value)
        if (url.scheme == "https" and url.username is None and url.password is None
                and url.hostname in {"www.jamendo.com", "jamendo.com", "ccmixter.org",
                                     "www.ccmixter.org", "commons.wikimedia.org"}):
            return value[:512]
    except ValueError:
        pass
    return ""


def read_history(path: str | Path) -> list[dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw[:MAX_RECENT] if isinstance(item, dict)]


def save_search(path: str | Path, *, reference_label: str, matches: list[dict],
                examined: int, weights: dict[str, int], adaptive: bool) -> None:
    """Persist descriptions and ranking, excluding credentials and raw fingerprints."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_label = str(reference_label)[:180]
    entry = {
        "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reference": safe_label,
        "analysed": max(0, int(examined)),
        "adaptive": bool(adaptive),
        "weights": {str(k): int(v) for k, v in weights.items()},
        "matches": [{"id": str(m.get("id", ""))[:40],
                     "artist": str(m.get("artist", ""))[:150],
                     "title": str(m.get("title", ""))[:150],
                     "source": str(m.get("source", "Jamendo"))[:80],
                     "listen_url": _safe_listen_url(m.get("listen_url")),
                     "score": float(m.get("score", 0))} for m in matches[:10]],
    }
    payload = json.dumps([entry, *read_history(path)][:MAX_RECENT], ensure_ascii=False)
    fd, temp_name = tempfile.mkstemp(prefix="history-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(payload)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
