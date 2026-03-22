"""Fast seat map extraction via Playwright DOM parsing.

Opens multiple seat map pages in parallel browser tabs,
extracts seat data from DOM classes (no screenshots, no AI).
"""

from __future__ import annotations

import asyncio
import logging

from playwright.async_api import async_playwright, Browser, Page

from movieseats.fetcher.theaters import ShowtimeInfo, TheaterInfo
from movieseats.seats.models import Seat, SeatMap, Showtime

logger = logging.getLogger(__name__)

# Max concurrent tabs to avoid overwhelming the browser
MAX_CONCURRENT = 6


async def fetch_all_seat_maps(
    theaters: list[TheaterInfo],
    context=None,
) -> list[tuple[Showtime, SeatMap]]:
    """Fetch seat maps for all showtimes across all theaters in parallel.

    Uses an existing browser context if provided.
    Returns list of (Showtime, SeatMap) tuples.
    """
    tasks_list: list[tuple[TheaterInfo, ShowtimeInfo]] = []
    for theater in theaters:
        for st in theater.showtimes:
            tasks_list.append((theater, st))

    if not tasks_list:
        return []

    logger.info("Fetching %d seat maps in parallel...", len(tasks_list))

    own_browser = False
    if context is None:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1366, "height": 768})
        own_browser = True

    try:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        results = await asyncio.gather(
            *[
                _fetch_one_seat_map(context, theater, st, semaphore)
                for theater, st in tasks_list
            ],
            return_exceptions=True,
        )
    finally:
        if own_browser:
            await browser.close()
            await pw.stop()

    # Filter successful results
    seat_maps: list[tuple[Showtime, SeatMap]] = []
    for result in results:
        if isinstance(result, tuple):
            seat_maps.append(result)
        elif isinstance(result, Exception):
            logger.warning("Seat map fetch failed: %s", str(result)[:80])

    logger.info("Successfully fetched %d/%d seat maps", len(seat_maps), len(tasks_list))
    return seat_maps


async def _fetch_one_seat_map(
    context,
    theater: TheaterInfo,
    st: ShowtimeInfo,
    semaphore: asyncio.Semaphore,
) -> tuple[Showtime, SeatMap]:
    """Fetch a single seat map from a TicketSeatMap page."""
    async with semaphore:
        page = await context.new_page()
        try:
            await page.goto(st.url, wait_until="domcontentloaded", timeout=10000)
            # Wait for seats to load (they load via JS after DOM)
            try:
                await page.wait_for_selector(".seatBlock", timeout=5000)
            except Exception:
                # Try a bit longer
                await asyncio.sleep(1)

            # Extract seats from DOM — properly classify all seat types
            raw_seats = await page.evaluate(r"""() => {
                const blocks = document.querySelectorAll('.seatBlock');
                const seats = [];
                blocks.forEach(el => {
                    const title = el.title || el.getAttribute('aria-label') || '';
                    const cls = el.className;

                    // Skip blank spaces (aisles, empty spots)
                    if (cls.includes('seatBlank')) return;

                    // Parse seat location from title
                    const match = title.match(/Seat\s+([A-Z])(\d+)/i);
                    if (!match) return;

                    // Classify seat status
                    const isAvailable = cls.includes('seatAvailable');
                    const isUnavailable = cls.includes('seatUnavailable');
                    const isSelected = cls.includes('seatSelected');

                    // Detect wheelchair/companion seats (title contains these words)
                    const isWheelchair = title.toLowerCase().includes('wheelchair')
                        || title.toLowerCase().includes('companion')
                        || cls.includes('wheelchair') || cls.includes('companion')
                        || cls.includes('ada');

                    // If it's not available, not unavailable, not selected —
                    // it's likely a wheelchair/companion/special seat
                    const isSpecial = !isAvailable && !isUnavailable && !isSelected;

                    seats.push({
                        row: match[1].toUpperCase(),
                        number: parseInt(match[2]),
                        available: isAvailable,
                        wheelchair: isWheelchair || isSpecial,
                    });
                });
                return seats;
            }""")

            # Extract metadata including price and format
            meta = await page.evaluate(r"""() => {
                const theater = document.querySelector('.seats-tickets-theatre-date');
                const time = document.querySelector('.seats-tickets-time');
                const formatEl = document.querySelector('.seats-tickets-showtime-details');
                const bodyText = document.body.innerText;

                // Extract price — look for "Tickets $XX.XX" pattern
                const priceMatch = bodyText.match(/Tickets?\s*\$(\d+\.?\d*)/);
                const price = priceMatch ? parseFloat(priceMatch[1]) : 0;

                // Extract format from showtime details
                let format = 'Standard';
                const formatText = formatEl ? formatEl.innerText : '';
                if (formatText.includes('IMAX')) format = 'IMAX';
                else if (formatText.includes('Cinemark XD') || formatText.includes('XD')) format = 'XD';
                else if (formatText.includes('SCREENX') || formatText.includes('ScreenX')) format = 'SCREENX';
                else if (formatText.includes('D-BOX')) format = 'D-BOX';
                else if (formatText.includes('3D') || formatText.includes('RealD')) format = '3D';
                else if (formatText.includes('Standard')) format = 'Standard';

                return {
                    theater: theater ? theater.innerText.trim().split(',')[0].trim() : '',
                    time: time ? time.innerText.trim() : '',
                    price: price,
                    format: format,
                };
            }""")

            await page.close()

            if not raw_seats:
                raise ValueError(f"No seats found for {st.url}")

            # Build SeatMap — exclude wheelchair-only rows from scoring
            rows_dict: dict[str, list[Seat]] = {}
            wheelchair_rows: set[str] = set()

            for s in raw_seats:
                seat = Seat(
                    row=s["row"],
                    number=s["number"],
                    status="available" if s["available"] else "wheelchair" if s.get("wheelchair") else "taken",
                )
                rows_dict.setdefault(s["row"], []).append(seat)

            # Detect rows that are entirely wheelchair/companion (no regular available or unavailable seats)
            for letter, seats_in_row in rows_dict.items():
                regular = [s for s in seats_in_row if s.status in ("available", "taken")]
                if not regular:
                    wheelchair_rows.add(letter)

            # Build rows list excluding wheelchair-only rows
            rows = []
            for letter in sorted(rows_dict.keys()):
                if letter in wheelchair_rows:
                    continue
                rows.append(sorted(rows_dict[letter], key=lambda s: s.number))

            seat_map = SeatMap(
                rows=rows,
                total_rows=len(rows),
                max_seats_per_row=max(len(r) for r in rows) if rows else 0,
            )

            if wheelchair_rows:
                logger.debug("Excluded wheelchair rows: %s", wheelchair_rows)

            theater_name = meta["theater"] or theater.name
            # Use format from page (more accurate) or fall back to theater name guess
            fmt = meta.get("format") or st.format
            price = meta.get("price", 0)

            showtime = Showtime(
                time=meta["time"] or st.time_display,
                date=st.showtime_dt.split("T")[0] if "T" in st.showtime_dt else "",
                format=fmt,
                price=price,
                theater_name=theater_name,
                chain="cinemark",
                url=st.url,
            )

            total = sum(len(r) for r in rows)
            avail = sum(1 for r in rows for s in r if s.status == "available")
            price_str = f" ${price:.2f}" if price else ""
            logger.info(
                "  %s %s (%s%s): %d/%d available",
                theater_name, st.time_display, fmt, price_str, avail, total,
            )

            return (showtime, seat_map)

        except Exception as e:
            await page.close()
            raise
