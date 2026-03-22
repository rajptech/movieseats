"""Tests for Pydantic data models."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from movieseats.seats.models import Seat, SeatMap, Showtime, SeatRecommendation, SearchResult


def test_seat_creation():
    seat = Seat(row="G", number=7, status="available")
    assert seat.row == "G"
    assert seat.number == 7
    assert seat.status == "available"


def test_seat_map_creation():
    rows = [
        [Seat(row="A", number=i, status="available") for i in range(1, 11)]
    ]
    sm = SeatMap(rows=rows, total_rows=1, max_seats_per_row=10)
    assert sm.total_rows == 1
    assert sm.max_seats_per_row == 10
    assert sm.screen_position == "top"


def test_showtime_defaults():
    st = Showtime(time="7:00 PM", date="2026-03-21")
    assert st.format == "Standard"
    assert st.theater_name == ""
    assert st.chain == ""


def test_search_result_defaults():
    sr = SearchResult(chain="amc")
    assert sr.recommendations == []
    assert sr.errors == []


def test_seat_recommendation():
    seat = Seat(row="G", number=8, status="available")
    showtime = Showtime(time="7:00 PM", date="2026-03-21", theater_name="AMC 16")
    rec = SeatRecommendation(
        showtime=showtime,
        seats=[seat],
        score=0.95,
        reasoning="Center seat, optimal row",
    )
    assert rec.score == 0.95
    assert len(rec.seats) == 1
