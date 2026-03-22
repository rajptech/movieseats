from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from movieseats.seats.models import Seat, SeatMap

logger = logging.getLogger(__name__)


@dataclass
class ParsedSeatData:
    """Parsed seat map with metadata."""
    seat_map: SeatMap
    theater_name: str = ""
    showtime: str = ""
    format: str = "Standard"


def parse_seat_map_response(claude_response: str) -> ParsedSeatData | None:
    """Parse Claude's JSON response into a SeatMap with metadata."""
    try:
        # Extract JSON from response (Claude may wrap it in markdown code blocks)
        text = claude_response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        data = json.loads(text)

        rows: list[list[Seat]] = []
        for row_data in data.get("rows", []):
            row_seats = []
            for seat_data in row_data:
                row_seats.append(
                    Seat(
                        row=seat_data["row"],
                        number=seat_data["number"],
                        status=seat_data.get("status", "available"),
                    )
                )
            if row_seats:
                rows.append(row_seats)

        if not rows:
            logger.warning("Parsed seat map has no rows")
            return None

        total_rows = data.get("total_rows", len(rows))
        max_seats = data.get("max_seats_per_row", max(len(r) for r in rows))
        screen_position = data.get("screen_position", "top")

        seat_map = SeatMap(
            rows=rows,
            total_rows=total_rows,
            max_seats_per_row=max_seats,
            screen_position=screen_position,
        )

        # Count stats
        total_seats = sum(len(r) for r in rows)
        available = sum(1 for r in rows for s in r if s.status == "available")
        taken = sum(1 for r in rows for s in r if s.status == "taken")
        logger.info(
            "Seat map: %d rows, %d total seats, %d available, %d taken",
            len(rows), total_seats, available, taken,
        )

        return ParsedSeatData(
            seat_map=seat_map,
            theater_name=data.get("theater_name", ""),
            showtime=data.get("showtime", ""),
            format=data.get("format", "Standard"),
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("Failed to parse seat map: %s", e)
        return None
