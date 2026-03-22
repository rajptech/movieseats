#!/usr/bin/env python3
"""MovieSeats v2 — Fast AI-powered movie seat finder.

Supports: Cinemark, Marcus Theatres (more chains coming).

Usage:
    python main.py --zipcode 75035 --movie "Dhurandhar" --seats 2
    python main.py -z 53719 -m "Project Hail Mary" -s 2 --date "22"
    python main.py -z 90001 -m "Hoppers" --no-ai
"""

import argparse
import asyncio
import logging
import time
import sys

from rich.console import Console
from playwright.async_api import async_playwright

from config import ANTHROPIC_API_KEY
from movieseats.fetcher.theaters import find_theaters_and_showtimes
from movieseats.fetcher.seats import fetch_all_seat_maps
from movieseats.fetcher.marcus import find_marcus_theaters, fetch_marcus_seat_map
from movieseats.brain import analyze_and_recommend
from movieseats.results import display_results
from movieseats.seats.models import SeatMap, Showtime

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find the best available movie theater seats near you — fast.",
    )
    parser.add_argument("--zipcode", "-z", required=True, help="US zipcode")
    parser.add_argument("--movie", "-m", required=True, help="Movie name")
    parser.add_argument("--seats", "-s", type=int, default=2, help="Number of seats (default: 2)")
    parser.add_argument("--date", "-d", default="", help="Date to search (e.g., '22' for the 22nd)")
    parser.add_argument("--time", "-t", default="evening", choices=["morning", "afternoon", "evening", "all"], help="Time preference (default: evening)")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI recommendation")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser.parse_args()


async def main():
    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    console.print(f"\n[bold blue]MovieSeats v2[/bold blue] — Fast Seat Finder")
    console.print(f"Searching for [bold]{args.movie}[/bold] near [bold]{args.zipcode}[/bold]...\n")

    start = time.time()
    all_seat_data: list[tuple[Showtime, SeatMap]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
        )

        date_text = args.date
        # Cinemark carousel shows dates as "3/22", "3/23", etc.
        # If user gives just day number, prepend current month
        if date_text and "/" not in date_text:
            import datetime
            month = datetime.date.today().month
            date_text = f"{month}/{date_text}"

        # === CINEMARK ===
        console.print("[dim]Searching Cinemark theaters...[/dim]")
        try:
            cinemark_theaters = await find_theaters_and_showtimes(
                context, args.zipcode, args.movie, date_text=date_text, time_pref=args.time
            )
            if cinemark_theaters:
                total_st = sum(len(t.showtimes) for t in cinemark_theaters)
                console.print(f"[cyan]Cinemark:[/cyan] {len(cinemark_theaters)} theaters, {total_st} showtimes")
                console.print("[dim]Fetching Cinemark seat maps...[/dim]")
                cinemark_seats = await fetch_all_seat_maps(cinemark_theaters, context)
                all_seat_data.extend(cinemark_seats)
            else:
                console.print("[dim]No Cinemark theaters found nearby[/dim]")
        except Exception as e:
            console.print(f"[dim]Cinemark search failed: {str(e)[:50]}[/dim]")

        # === MARCUS ===
        console.print("[dim]Searching Marcus theaters...[/dim]")
        try:
            marcus_results = await find_marcus_theaters(
                context, args.zipcode, args.movie, date_text=date_text
            )
            if marcus_results:
                total_marcus = sum(len(sts) for _, sts in marcus_results)
                console.print(f"[cyan]Marcus:[/cyan] {len(marcus_results)} theaters, {total_marcus} showtimes")
                console.print("[dim]Fetching Marcus seat maps...[/dim]")
                for theater_name, showtimes in marcus_results:
                    for st in showtimes:
                        result = await fetch_marcus_seat_map(context, st)
                        if result:
                            all_seat_data.append(result)
            else:
                console.print("[dim]No Marcus theaters found nearby[/dim]")
        except Exception as e:
            console.print(f"[dim]Marcus search failed: {str(e)[:50]}[/dim]")

        await browser.close()

    if not all_seat_data:
        console.print("\n[red]No seat data found from any theater chain.[/red]")
        console.print("[dim]This movie may not be playing near this zipcode, or the chains we support (Cinemark, Marcus) don't have theaters there.[/dim]")
        sys.exit(1)

    fetch_time = time.time() - start
    console.print(f"[dim]Data fetched in {fetch_time:.1f}s[/dim]")

    # AI recommendation
    ai_rec = ""
    if not args.no_ai and ANTHROPIC_API_KEY:
        console.print("[dim]AI analyzing best seats...[/dim]")
        ai_rec = await analyze_and_recommend(all_seat_data, args.movie, args.seats)

    elapsed = time.time() - start
    display_results(all_seat_data, args.movie, args.zipcode, args.seats, ai_rec, elapsed)


if __name__ == "__main__":
    asyncio.run(main())
