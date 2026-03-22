from __future__ import annotations
from pydantic import BaseModel


class Seat(BaseModel):
    row: str  # "A", "B", ... "Z"
    number: int  # 1-based seat number
    status: str  # "available", "taken", "wheelchair", "companion", "blocked"


class SeatMap(BaseModel):
    rows: list[list[Seat]]
    total_rows: int
    max_seats_per_row: int
    screen_position: str = "top"  # "top" or "bottom" — where the screen is in the grid


class Showtime(BaseModel):
    time: str  # "7:30 PM"
    date: str  # "2026-03-21"
    format: str = "Standard"  # "Standard", "IMAX", "Dolby", "XD", "3D"
    price: float = 0.0  # ticket price in USD
    theater_name: str = ""
    chain: str = ""
    auditorium: str = ""
    url: str = ""  # URL of the showtime page


class SeatRecommendation(BaseModel):
    showtime: Showtime
    seats: list[Seat]
    score: float
    reasoning: str


class SearchResult(BaseModel):
    chain: str
    theater_name: str = ""
    recommendations: list[SeatRecommendation] = []
    errors: list[str] = []
