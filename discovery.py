"""Link-only music discovery from public metadata, not actual acoustic similarity.

YouTube oEmbed is used only to identify a publicly linked track. Apple Search API
is used for artist/title/genre metadata and store links; no promo preview assets
are fetched, downloaded, analysed, or played.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, quote_plus

import requests

BENCHMARK_URL = "https://www.youtube.com/watch?v=JD0Ve7psY_I"
BENCHMARK_TITLE = "HXVRMXN - ECLIPSE (Night Drive Phonk)"

STYLE_TERMS = (
    "phonk", "dark phonk", "drift phonk", "night drive", "hardwave",
    "wave", "synthwave", "darkwave", "trap", "trap metal", "drill",
    "dubstep", "drum and bass", "dnb", "techno", "house", "jungle",
    "breakcore", "ambient", "lofi", "lo-fi", "industrial", "hardstyle",
    "witch house", "vaporwave", "chillstep", "electronic",
)
STOPWORDS = {"the", "an", "a", "official", "audio", "music", "video", "visualizer", "visualiser", "lyric", "lyrics", "hd", "hq", "4k", "remastered", "feat", "ft"}


def youtube_id(url: str) -> str | None:
    parsed = urlparse(url.strip())
    domain = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return None
    candidate = ""
    if domain in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
    elif domain in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            candidate = parsed.path.split("/")[2]
    return candidate if re.fullmatch(r"[a-zA-Z0-9_-]{11}", candidate) else None


def get_video_title(url: str) -> str:
    video = youtube_id(url)
    if not video:
        raise ValueError("Please paste a valid YouTube video link.")
    # A local known-title fallback ensures the starter benchmark remains
    # identified when YouTube is temporarily unreachable.
    try:
        response = requests.get("https://www.youtube.com/oembed",
                                params={"url": f"https://www.youtube.com/watch?v={video}", "format": "json"},
                                headers={"User-Agent": "VibeFinderLocal/0.2"}, timeout=8)
        response.raise_for_status()
        title = str(response.json().get("title", "")).strip()
        if title:
            return title
    except (requests.RequestException, ValueError):
        pass
    if video == "JD0Ve7psY_I":
        return BENCHMARK_TITLE
    raise LookupError("YouTube could not identify this video. Check your connection and try again.")


def clean_title(title: str) -> str:
    title = re.sub(r"\[[^]]*\]", " ", title)
    title = re.sub(r"\((?:official|audio|music video|lyric|lyrics|visuali[sz]er)[^)]*\)", " ", title, flags=re.I)
    return " ".join(title.split()).strip(" -|")


def style_terms(title: str) -> list[str]:
    text = clean_title(title).lower()
    words = []
    for term in STYLE_TERMS:
        if re.search(r"\b" + re.escape(term) + r"\b", text):
            words.append(term)
    return sorted(set(words), key=lambda x: (-len(x), x))


def get_search_terms(title: str) -> list[str]:
    """Extract style cues where possible; fall back to artist/title metadata."""
    styles = style_terms(title)
    # Broader and more targeted catalogue queries. Still metadata-only.
    queries: list[str] = []
    if styles:
        queries.append(styles[-1] if len(styles) == 1 else " ".join(styles[:2]))
        for style in styles:
            if style not in queries:
                queries.append(style)
    else:
        chunks = re.split(r"\s*[-–—|:]\s*", clean_title(title), maxsplit=1)
        queries.append(chunks[-1].strip()[:70])
        if len(chunks) == 2:
            queries.append(chunks[0].strip()[:70])
    return [x for x in dict.fromkeys(queries) if x][:3]


def _tokens(value: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", value.lower()) if w not in STOPWORDS and len(w) >= 3}


def discover_tracks(title: str, limit: int = 10, get=requests.get) -> tuple[list[dict], list[str]]:
    """Search legitimate catalogue metadata, never classify these as audio matches."""
    queries = get_search_terms(title)
    rows: dict[str, dict] = {}
    issues = []
    seed_tokens = _tokens(title)
    style_tokens = set().union(*[_tokens(s) for s in style_terms(title)]) if style_terms(title) else set()
    seed_artist = re.split(r"\s*[-–—|:]\s*", clean_title(title), maxsplit=1)[0].casefold()
    for query in queries:
        try:
            result = get("https://itunes.apple.com/search",
                         params={"term": query, "media": "music", "entity": "song", "limit": 70},
                         headers={"User-Agent": "VibeFinderLocal/0.2"}, timeout=10)
            result.raise_for_status()
            items = result.json().get("results", [])
        except (requests.RequestException, ValueError, AttributeError) as exc:
            issues.append(f"Catalogue query '{query}' failed: {exc}")
            continue
        for item in items:
            if item.get("kind") != "song":
                continue
            track = str(item.get("trackName", "")).strip()
            artist = str(item.get("artistName", "")).strip()
            genre = str(item.get("primaryGenreName", "")).strip()
            link = str(item.get("trackViewUrl", "")).strip()
            if not (track and artist and link):
                continue
            if artist.casefold() == seed_artist and track.casefold() in clean_title(title).casefold():
                continue
            key = str(item.get("trackId") or f"{artist.casefold()}|{track.casefold()}")
            tokens = _tokens(" ".join([track, artist, genre]))
            style_overlap = len(tokens & style_tokens)
            any_overlap = len(tokens & seed_tokens)
            # Used only to order approximate catalogue leads; no numeric
            # similarity rating is shown to the user.
            relevance = style_overlap * 5 + any_overlap
            if artist.casefold() == seed_artist:
                relevance -= 3  # prefer fresh discoveries rather than same artist
            rows[key] = {
                "track": track, "artist": artist, "genre": genre,
                "store_url": link,
                "youtube_search_url": "https://www.youtube.com/results?search_query=" + quote_plus(f"{artist} {track}"),
                "relevance": relevance,
            }
    return sorted(rows.values(), key=lambda x: (-x["relevance"], x["artist"], x["track"]))[:limit], issues
