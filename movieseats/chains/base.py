from __future__ import annotations

from pydantic import BaseModel


class ChainConfig(BaseModel):
    """Configuration for a theater chain — navigation hints for the agent."""

    name: str
    url: str
    search_hints: str  # How to search for movies on this site
    showtime_hints: str  # How showtimes appear on this site
    seat_map_hints: str  # How to interpret the seat map
    known_popups: list[str] = []  # CSS selectors for dismissible popups
