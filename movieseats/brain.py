"""AI brain — Claude analyzes all seat data and gives smart recommendation."""

from __future__ import annotations

import logging

import anthropic

from movieseats.seats.models import SeatMap, Showtime, SeatRecommendation
from movieseats.seats.scorer import find_best_seats
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)


async def analyze_and_recommend(
    seat_data: list[tuple[Showtime, SeatMap]],
    movie_name: str,
    num_seats: int,
    user_preferences: str = "",
) -> str:
    """Send all seat data to Claude for intelligent analysis.

    Returns a natural language recommendation.
    """
    if not seat_data:
        return "No seat data available to analyze."

    # First, score all seats with math (instant)
    all_recs: list[tuple[Showtime, SeatRecommendation]] = []
    summary_lines = []

    for showtime, seat_map in seat_data:
        recs = find_best_seats(seat_map, showtime, num_seats, top_n=3)
        for rec in recs:
            all_recs.append((showtime, rec))

        # Build summary for Claude
        total = sum(len(r) for r in seat_map.rows)
        avail = sum(1 for r in seat_map.rows for s in r if s.status == "available")
        row_summary = []
        for row in seat_map.rows:
            if row:
                letter = row[0].row
                row_avail = sum(1 for s in row if s.status == "available")
                row_summary.append(f"Row {letter}: {row_avail}/{len(row)}")

        best = recs[0] if recs else None
        best_str = ""
        if best:
            seats_str = ", ".join(f"{s.row}{s.number}" for s in best.seats)
            best_str = f"Best pair: {seats_str} (score: {best.score:.2f})"

        summary_lines.append(
            f"- {showtime.theater_name} | {showtime.time} | {showtime.format}\n"
            f"  Seats: {avail}/{total} available | {' | '.join(row_summary)}\n"
            f"  {best_str}"
        )

    # Sort all recommendations by score
    all_recs.sort(key=lambda x: x[1].score, reverse=True)

    # Build prompt for Claude
    data_summary = "\n".join(summary_lines)
    top_picks = ""
    for i, (st, rec) in enumerate(all_recs[:10], 1):
        seats_str = ", ".join(f"{s.row}{s.number}" for s in rec.seats)
        top_picks += (
            f"{i}. {st.theater_name} | {st.time} ({st.format}) | "
            f"Seats {seats_str} | Score: {rec.score:.2f}\n"
        )

    prompt = f"""You are a movie seat advisor. A user wants {num_seats} seats for "{movie_name}".
{f'User preferences: {user_preferences}' if user_preferences else ''}

Here is the real-time seat availability data across all nearby theaters:

{data_summary}

Top {min(10, len(all_recs))} seat options ranked by quality (center position + row depth):
{top_picks}

Scoring: seats are scored 0-1.25 based on center position (40%), row depth from screen (35%), and adjacency (25%). The ideal row is ~65% back from the screen.

Give a brief, confident recommendation:
1. Your TOP PICK and why (1-2 sentences)
2. A RUNNER UP alternative (1 sentence)
3. Any showtime/theater to AVOID and why (1 sentence)

Be specific with seat numbers. Keep it under 100 words total."""

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        logger.error("Claude analysis failed: %s", e)
        # Fallback: return math-based recommendation
        if all_recs:
            st, rec = all_recs[0]
            seats_str = ", ".join(f"{s.row}{s.number}" for s in rec.seats)
            return (
                f"Best seats: {seats_str} at {st.theater_name}, "
                f"{st.time} ({st.format}). Score: {rec.score:.2f}/1.25"
            )
        return "No good seats found."
