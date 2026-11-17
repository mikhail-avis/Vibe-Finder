"""Feedback-driven second-pass discovery planning; no network or audio access.

This module selects *where* to search next, not whether a track is musically
similar. Only actual acoustic matches from the first pass determine focus tags.
"""
from __future__ import annotations

from collections import defaultdict


def search_budgets(total: int, adaptive: bool = True) -> tuple[int, int]:
    """Keep one-pass scans small; adaptive scans reserve 40% for exploration."""
    if not 4 <= total <= 60:
        raise ValueError("Candidate budget must be between 4 and 60")
    if not adaptive or total < 8:
        return total, 0
    first = max(4, (total * 3 + 4) // 5)
    return first, total - first


def focus_styles(matches: list[dict], known_styles: tuple[str, ...], limit: int = 2) -> tuple[str, ...]:
    """Choose styles represented by high-ranked acoustically analysed tracks.

    Distinct top results contribute more than repeated weak matches. Unrecognised
    tags are ignored; ties use original tag order for reproducible behaviour.
    """
    if not matches or limit < 1:
        return ()
    options = set(known_styles)
    points = defaultdict(float)
    # Keep the shortlist diverse; favour the highest-quality matches.
    for rank, item in enumerate(sorted(matches, key=lambda m: float(m.get("score", 0)), reverse=True)[:8]):
        style = item.get("source_style")
        if style in options:
            points[style] += max(0.0, float(item.get("score", 0))) / (rank + 1)
    return tuple(sorted(points, key=lambda s: (-points[s], known_styles.index(s)))[:limit])
