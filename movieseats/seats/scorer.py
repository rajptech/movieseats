from __future__ import annotations

import logging

from movieseats.seats.models import Seat, SeatMap, SeatRecommendation, Showtime
from config import WEIGHT_CENTER, WEIGHT_ROW, WEIGHT_ADJACENCY, IDEAL_ROW_RATIO

logger = logging.getLogger(__name__)


def _center_score(seat_number: int, min_seat: int, max_seat: int) -> float:
    """Score 0.0-1.0: 1.0 = dead center of the row, 0.0 = edge."""
    if max_seat <= min_seat:
        return 1.0
    # Normalize seat position to 0.0 (leftmost) - 1.0 (rightmost)
    position = (seat_number - min_seat) / (max_seat - min_seat)
    return max(0.0, 1.0 - abs(position - 0.5) * 2)


def _row_score(row_index: int, total_rows: int) -> float:
    """Score 0.0-1.0: 1.0 = ideal row (~65% back), 0.0 = worst row."""
    if total_rows <= 1:
        return 1.0
    ideal_row = IDEAL_ROW_RATIO * (total_rows - 1)
    distance = abs(row_index - ideal_row) / (total_rows - 1)
    return max(0.0, 1.0 - distance)


def _row_index(row_letter: str) -> int:
    """Convert row letter to 0-based index. Handles A-Z."""
    return ord(row_letter.upper()) - ord("A")


def score_single_seat(seat: Seat, min_seat: int, max_seat: int, total_rows: int) -> float:
    """Score a single seat based on center position and row."""
    cs = _center_score(seat.number, min_seat, max_seat)
    rs = _row_score(_row_index(seat.row), total_rows)
    return WEIGHT_CENTER * cs + WEIGHT_ROW * rs


def find_best_seats(
    seat_map: SeatMap,
    showtime: Showtime,
    num_seats: int = 2,
    top_n: int = 5,
) -> list[SeatRecommendation]:
    """Find the best available seat groups in a seat map.

    For num_seats=1, scores individual seats.
    For num_seats>1, finds contiguous groups in the same row.
    Returns top_n recommendations sorted by score descending.
    """
    candidates: list[SeatRecommendation] = []

    # Log seat availability per row for debugging
    for row_seats in seat_map.rows:
        if row_seats:
            row_letter = row_seats[0].row
            avail = sum(1 for s in row_seats if s.status == "available")
            total = len(row_seats)
            logger.info("Row %s: %d/%d available", row_letter, avail, total)

    for row_seats in seat_map.rows:
        if not row_seats:
            continue

        available = [s for s in row_seats if s.status == "available"]
        if len(available) < num_seats:
            continue

        # Use actual seat number range for center calculation
        all_numbers = [s.number for s in row_seats]
        min_seat = min(all_numbers)
        max_seat = max(all_numbers)

        # Find contiguous runs of available seats
        runs: list[list[Seat]] = []
        current_run: list[Seat] = [available[0]]

        for i in range(1, len(available)):
            if available[i].number == available[i - 1].number + 1:
                current_run.append(available[i])
            else:
                if len(current_run) >= num_seats:
                    runs.append(current_run)
                current_run = [available[i]]

        if len(current_run) >= num_seats:
            runs.append(current_run)

        # Score every window of size num_seats within each run
        for run in runs:
            for start in range(len(run) - num_seats + 1):
                group = run[start : start + num_seats]
                individual_scores = [
                    score_single_seat(s, min_seat, max_seat, seat_map.total_rows)
                    for s in group
                ]
                avg_score = sum(individual_scores) / len(individual_scores)
                # Adjacency bonus: all contiguous = full bonus
                group_score = avg_score + WEIGHT_ADJACENCY * 1.0

                seat_labels = ", ".join(f"{s.row}{s.number}" for s in group)
                row_letter = group[0].row
                row_idx = _row_index(row_letter)
                row_pct = round((row_idx + 1) / seat_map.total_rows * 100)
                cs_avg = sum(_center_score(s.number, min_seat, max_seat) for s in group) / len(group)
                rs = _row_score(row_idx, seat_map.total_rows)

                logger.debug(
                    "  Candidate: %s | center=%.2f row=%.2f (row %s, %d%% back) total=%.3f",
                    seat_labels, cs_avg, rs, row_letter, row_pct, group_score,
                )

                candidates.append(
                    SeatRecommendation(
                        showtime=showtime,
                        seats=group,
                        score=round(group_score, 3),
                        reasoning=f"Seats {seat_labels} — Row {row_letter} ({row_pct}% back), center score {cs_avg:.0%}",
                    )
                )

    candidates.sort(key=lambda r: r.score, reverse=True)
    return candidates[:top_n]
