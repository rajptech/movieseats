"""Tests for the seat scoring algorithm."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from movieseats.seats.models import Seat, SeatMap, Showtime
from movieseats.seats.scorer import find_best_seats, score_single_seat


def _make_seat(row: str, number: int, status: str = "available") -> Seat:
    return Seat(row=row, number=number, status=status)


def _make_showtime() -> Showtime:
    return Showtime(time="7:00 PM", date="2026-03-21", theater_name="Test Theater", chain="test")


def _make_seat_map() -> SeatMap:
    """Create a 10-row, 15-seat theater with some taken seats."""
    rows = []
    for r_idx, row_letter in enumerate("ABCDEFGHIJ"):
        row = []
        for seat_num in range(1, 16):
            # Take some seats: first 2 rows fully taken, center of row G taken
            if r_idx < 2:
                status = "taken"
            elif row_letter == "G" and 7 <= seat_num <= 9:
                status = "taken"
            else:
                status = "available"
            row.append(_make_seat(row_letter, seat_num, status))
        rows.append(row)
    return SeatMap(rows=rows, total_rows=10, max_seats_per_row=15)


def test_center_seats_score_higher():
    """Center seats should score higher than edge seats."""
    center = score_single_seat(_make_seat("G", 8), 1, 15, 10)
    edge = score_single_seat(_make_seat("G", 1), 1, 15, 10)
    assert center > edge, f"Center ({center}) should beat edge ({edge})"


def test_optimal_row_scores_higher():
    """Row ~65% back should score higher than front row."""
    optimal = score_single_seat(_make_seat("G", 8), 1, 15, 10)  # row 7/10 = 70%
    front = score_single_seat(_make_seat("A", 8), 1, 15, 10)  # row 1/10 = 10%
    assert optimal > front, f"Optimal row ({optimal}) should beat front ({front})"


def test_find_best_seats_returns_results():
    """Should return recommendations for available seats."""
    seat_map = _make_seat_map()
    showtime = _make_showtime()
    results = find_best_seats(seat_map, showtime, num_seats=2, top_n=5)

    assert len(results) > 0, "Should find at least one recommendation"
    assert len(results) <= 5, "Should return at most top_n results"


def test_find_best_seats_avoids_taken():
    """Recommended seats should all be available."""
    seat_map = _make_seat_map()
    showtime = _make_showtime()
    results = find_best_seats(seat_map, showtime, num_seats=2)

    for rec in results:
        for seat in rec.seats:
            assert seat.status == "available", f"Seat {seat.row}{seat.number} should be available"


def test_find_best_seats_adjacent():
    """Recommended seats for num_seats>1 should be adjacent."""
    seat_map = _make_seat_map()
    showtime = _make_showtime()
    results = find_best_seats(seat_map, showtime, num_seats=3)

    for rec in results:
        numbers = [s.number for s in rec.seats]
        for i in range(len(numbers) - 1):
            assert numbers[i + 1] == numbers[i] + 1, (
                f"Seats should be adjacent: {numbers}"
            )


def test_find_best_seats_same_row():
    """All seats in a recommendation should be in the same row."""
    seat_map = _make_seat_map()
    showtime = _make_showtime()
    results = find_best_seats(seat_map, showtime, num_seats=2)

    for rec in results:
        rows = set(s.row for s in rec.seats)
        assert len(rows) == 1, f"All seats should be same row, got {rows}"


def test_scores_are_ordered():
    """Results should be sorted by score descending."""
    seat_map = _make_seat_map()
    showtime = _make_showtime()
    results = find_best_seats(seat_map, showtime, num_seats=2)

    for i in range(len(results) - 1):
        assert results[i].score >= results[i + 1].score, (
            f"Results should be sorted: {results[i].score} >= {results[i+1].score}"
        )
