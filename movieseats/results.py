"""Results display with rich formatting."""

from __future__ import annotations

import time

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from movieseats.seats.models import SeatMap, Showtime, SeatRecommendation
from movieseats.seats.scorer import find_best_seats

console = Console()


def display_results(
    seat_data: list[tuple[Showtime, SeatMap]],
    movie_name: str,
    zipcode: str,
    num_seats: int,
    ai_recommendation: str,
    elapsed: float,
) -> None:
    """Display search results with rich formatting."""

    console.print()
    console.print(
        Panel(
            f"[bold]Movie:[/bold] {movie_name}\n"
            f"[bold]Zipcode:[/bold] {zipcode}\n"
            f"[bold]Seats:[/bold] {num_seats}\n"
            f"[bold]Time:[/bold] {elapsed:.1f} seconds",
            title="MovieSeats",
            border_style="blue",
        )
    )

    if not seat_data:
        console.print("\n[red bold]No seat data found.[/red bold]\n")
        return

    # Collect all recommendations
    all_recs: list[tuple[Showtime, SeatRecommendation]] = []
    for showtime, seat_map in seat_data:
        recs = find_best_seats(seat_map, showtime, num_seats, top_n=2)
        for rec in recs:
            all_recs.append((showtime, rec))

    all_recs.sort(key=lambda x: x[1].score, reverse=True)

    # Availability summary
    console.print()
    summary_table = Table(title="Theater Availability", show_lines=True)
    summary_table.add_column("Theater", style="cyan", width=30)
    summary_table.add_column("Time", style="yellow", width=10)
    summary_table.add_column("Format", style="magenta", width=10)
    summary_table.add_column("Available", justify="center", width=10)
    summary_table.add_column("Total", justify="center", width=8)

    for showtime, seat_map in seat_data:
        total = sum(len(r) for r in seat_map.rows)
        avail = sum(1 for r in seat_map.rows for s in r if s.status == "available")
        avail_style = "green" if avail > 10 else "yellow" if avail > 3 else "red"
        summary_table.add_row(
            showtime.theater_name,
            showtime.time,
            showtime.format,
            f"[{avail_style}]{avail}[/{avail_style}]",
            str(total),
        )

    console.print(summary_table)

    # Top seats table
    if all_recs:
        console.print()
        seats_table = Table(title="Best Available Seats", show_lines=True)
        seats_table.add_column("Rank", justify="center", style="bold", width=5)
        seats_table.add_column("Theater", style="cyan", width=28)
        seats_table.add_column("Time", style="yellow", width=10)
        seats_table.add_column("Format", style="magenta", width=10)
        seats_table.add_column("Seats", style="green bold", width=12)
        seats_table.add_column("Score", justify="center", style="bold", width=7)
        seats_table.add_column("Details", style="dim", width=28)

        for rank, (st, rec) in enumerate(all_recs[:10], 1):
            seat_labels = ", ".join(f"{s.row}{s.number}" for s in rec.seats)
            seats_table.add_row(
                str(rank),
                st.theater_name,
                st.time,
                st.format,
                seat_labels,
                f"{rec.score:.2f}",
                rec.reasoning,
            )

        console.print(seats_table)

    # AI Recommendation
    if ai_recommendation:
        console.print()
        console.print(
            Panel(
                ai_recommendation,
                title="AI Recommendation",
                border_style="green",
            )
        )

    console.print()
