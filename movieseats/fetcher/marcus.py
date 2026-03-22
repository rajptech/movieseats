"""Marcus Theatres fetcher — discovery + seat extraction."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from playwright.async_api import BrowserContext, Page

from movieseats.seats.models import Seat, SeatMap, Showtime

logger = logging.getLogger(__name__)

MARCUS_BASE = "https://www.marcustheatres.com"


@dataclass
class MarcusShowtime:
    theater_name: str
    theater_slug: str
    time_display: str
    movie_slug: str
    date: str  # "2026-03-22"


async def find_marcus_theaters(
    context: BrowserContext,
    zipcode: str,
    movie_name: str,
    date_text: str = "",
    max_per_theater: int = 3,
) -> list[tuple[str, list[MarcusShowtime]]]:
    """Find Marcus theaters near zipcode showing a movie.

    Step 1: Go to movie page to discover which theaters show it.
    Step 2: For each theater, get showtimes.
    """
    page = await context.new_page()
    results: list[tuple[str, list[MarcusShowtime]]] = []

    try:
        # Step 1: Go to the theater-locations page and search for nearby theaters
        # The direct approach: visit known Marcus theater pages near the zipcode
        # Marcus theater slugs follow the pattern: {name}-{city}
        # We try the movie page for each known theater and see which loads showtimes

        # First, find the movie slug by trying a known Marcus theater page
        known_slugs = await _discover_marcus_theaters_via_search(page, zipcode)

        if not known_slugs:
            logger.info("No Marcus theaters found near %s", zipcode)
            return []

        # Step 2: Find movie slug from first working theater
        movie_slug = None
        for slug, name in known_slugs:
            page2 = await context.new_page()
            try:
                movie_slug = await _find_marcus_movie_slug(page2, movie_name, slug)
                if movie_slug:
                    break
            finally:
                await page2.close()

        if not movie_slug:
            # Try common slug patterns
            movie_slug = _name_to_slug(movie_name)
            logger.info("Guessing Marcus movie slug: %s", movie_slug)

        # Step 3: For each theater, get showtimes
        for slug, theater_name in known_slugs[:5]:
            page2 = await context.new_page()
            try:
                showtimes = await _get_marcus_showtimes(
                    page2, slug, movie_slug, theater_name, date_text
                )
                if showtimes:
                    picked = _pick_best_marcus_showtimes(showtimes, max_per_theater)
                    results.append((theater_name, picked))
                    logger.info("  %s: %d showtimes", theater_name, len(picked))
            except Exception as e:
                logger.warning("Marcus %s failed: %s", theater_name, str(e)[:60])
            finally:
                await page2.close()

    except Exception as e:
        logger.error("Marcus discovery failed: %s", e)
    finally:
        await page.close()

    return results


async def _discover_marcus_theaters_via_search(page: Page, zipcode: str) -> list[tuple[str, str]]:
    """Discover Marcus theaters near a zipcode.

    Uses Marcus's own movie page which lists nearby theaters.
    Returns list of (slug, name) tuples.
    """
    import re

    # Go to Marcus homepage — it often shows nearby theaters
    await page.goto(f"{MARCUS_BASE}/movies/book-now", wait_until="networkidle", timeout=20000)
    await page.wait_for_timeout(3000)

    # Try to find a location input and enter zipcode
    inputs = page.locator("input:visible")
    count = await inputs.count()
    for i in range(count):
        inp = inputs.nth(i)
        placeholder = await inp.get_attribute("placeholder") or ""
        if "zip" in placeholder.lower() or "city" in placeholder.lower() or "location" in placeholder.lower():
            await inp.fill(zipcode)
            await inp.press("Enter")
            await page.wait_for_timeout(3000)
            break

    # Extract theater slugs from any links on the page
    slugs = await page.evaluate(r"""() => {
        const links = document.querySelectorAll('a[href*="/theatre-locations/"]');
        const seen = new Set();
        const results = [];
        links.forEach(a => {
            const match = a.href.match(/theatre-locations\/([^/?]+)/);
            if (match && !seen.has(match[1])) {
                seen.add(match[1]);
                results.push({slug: match[1], name: a.innerText.trim() || match[1]});
            }
        });
        return results;
    }""")

    if slugs:
        return [(s["slug"], s["name"]) for s in slugs]

    # Fallback: search Google for Marcus theaters near zipcode
    try:
        await page.goto(
            f"https://www.google.com/search?q=site:marcustheatres.com+theatre-locations+near+{zipcode}",
            wait_until="domcontentloaded",
            timeout=10000,
        )
        await page.wait_for_timeout(2000)

        google_slugs = await page.evaluate(r"""() => {
            const links = document.querySelectorAll('a[href*="marcustheatres.com/theatre-locations/"]');
            const seen = new Set();
            const results = [];
            links.forEach(a => {
                const match = a.href.match(/theatre-locations\/([^/?&#]+)/);
                if (match && !seen.has(match[1])) {
                    seen.add(match[1]);
                    const name = match[1].replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                    results.push({slug: match[1], name});
                }
            });
            return results;
        }""")

        if google_slugs:
            return [(s["slug"], s["name"]) for s in google_slugs]
    except Exception:
        pass

    # Last resort: try common Madison, WI theaters if zip matches
    # This is a known-good list for common zipcodes
    common = {
        "53": [  # Wisconsin area codes
            ("point-cinema-madison", "Point Cinema Madison"),
            ("palace-cinema-sun-prairie", "Palace Cinema Sun Prairie"),
        ],
    }
    prefix = zipcode[:2]
    if prefix in common:
        logger.info("Using known Marcus theaters for zip prefix %s", prefix)
        return common[prefix]

    return []


def _name_to_slug(name: str) -> str:
    """Convert theater name to URL slug. E.g., 'Point Cinema' -> 'point-cinema-madison'."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9\s]', '', slug)
    slug = re.sub(r'\s+', '-', slug)
    return slug


async def _find_marcus_movie_slug(page: Page, movie_name: str, theater_slug: str) -> str | None:
    """Find movie slug on Marcus."""
    if theater_slug:
        url = f"{MARCUS_BASE}/movies/book-now?theatre={theater_slug}"
    else:
        url = f"{MARCUS_BASE}/movies/book-now"
    await page.goto(url, wait_until="networkidle", timeout=20000)
    await page.wait_for_timeout(2000)

    slug = await page.evaluate("""(name) => {
        const links = document.querySelectorAll('a[href*="/movies/"]');
        const nameLower = name.toLowerCase();
        const words = nameLower.split(/\\s+/);
        const skip = ['book-now', 'movies', ''];
        for (const link of links) {
            const text = link.innerText.toLowerCase();
            if (text.includes(nameLower)) {
                const match = link.href.match(/\\/movies\\/([^/?]+)/);
                if (match && !skip.includes(match[1])) return match[1];
            }
        }
        for (const link of links) {
            const text = (link.innerText + ' ' + link.href).toLowerCase();
            for (const word of words) {
                if (word.length > 3 && text.includes(word)) {
                    const match = link.href.match(/\\/movies\\/([^/?]+)/);
                    if (match && !skip.includes(match[1])) return match[1];
                }
            }
        }
        return null;
    }""", movie_name)

    if slug:
        logger.info("Found Marcus movie slug: %s", slug)
    return slug


async def _get_marcus_showtimes(
    page: Page, theater_slug: str, movie_slug: str, theater_name: str, date_text: str,
) -> list[MarcusShowtime]:
    """Get showtimes for a movie at a Marcus theater."""
    url = f"{MARCUS_BASE}/movies/{movie_slug}?theatre={theater_slug}"
    await page.goto(url, wait_until="networkidle", timeout=20000)
    await page.wait_for_timeout(1500)

    # Click date if specified
    if date_text:
        try:
            await page.locator(f'p.date:text("{date_text}")').first.click(force=True, timeout=3000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

    # Extract showtimes
    times = await page.evaluate(r"""() => {
        const els = document.querySelectorAll('a, button, [class*="showtime"]');
        return Array.from(els)
            .filter(e => /\d{1,2}:\d{2}\s*(am|pm|AM|PM)/i.test(e.innerText) && e.innerText.trim().length < 20)
            .map(e => e.innerText.trim());
    }""")

    return [
        MarcusShowtime(
            theater_name=theater_name,
            theater_slug=theater_slug,
            time_display=t,
            movie_slug=movie_slug,
            date=date_text,
        )
        for t in times
    ]


def _pick_best_marcus_showtimes(showtimes: list[MarcusShowtime], n: int) -> list[MarcusShowtime]:
    """Pick best spread of showtimes."""
    def _is_evening(st: MarcusShowtime) -> bool:
        return "PM" in st.time_display.upper() and any(
            st.time_display.startswith(h) for h in ["6:", "7:", "8:", "9:"]
        )

    evening = [s for s in showtimes if _is_evening(s)]
    rest = [s for s in showtimes if not _is_evening(s)]
    return (evening + rest)[:n]


async def fetch_marcus_seat_map(
    context: BrowserContext,
    showtime: MarcusShowtime,
) -> tuple[Showtime, SeatMap] | None:
    """Fetch seat map for a single Marcus showtime."""
    page = await context.new_page()
    try:
        url = f"{MARCUS_BASE}/movies/{showtime.movie_slug}?theatre={showtime.theater_slug}"
        await page.goto(url, wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(1000)

        # Click date
        if showtime.date:
            try:
                await page.locator(f'p.date:text("{showtime.date}")').first.click(force=True, timeout=3000)
                await page.wait_for_timeout(2000)
            except Exception:
                pass

        # Click the showtime
        await page.get_by_text(showtime.time_display, exact=True).first.click(force=True, timeout=5000)
        await page.wait_for_timeout(5000)

        # Parse seats from DOM
        raw_seats = await page.evaluate("""() => {
            const rows = document.querySelectorAll('.seat-row');
            const result = [];
            rows.forEach(row => {
                const titleEl = row.querySelector('.seat-row-title');
                const rowName = titleEl?.innerText?.trim() || '';
                if (!rowName || rowName.length > 3) return;

                let seatNum = 0;
                row.querySelectorAll('.seat:not(.seat-row-title)').forEach(seat => {
                    const cls = seat.className;
                    if (cls.includes('is-passage')) return;
                    seatNum++;
                    const isSold = cls.includes('is-sold');
                    const isDisabled = cls.includes('is-disabled');
                    const isReserved = cls.includes('is-reserved');
                    const status = (isSold || isDisabled || isReserved) ? 'taken' : 'available';
                    result.push({row: rowName, number: seatNum, status});
                });
            });
            return result;
        }""")

        if not raw_seats:
            return None

        # Build SeatMap
        rows_dict: dict[str, list[Seat]] = {}
        for s in raw_seats:
            seat = Seat(row=s["row"], number=s["number"], status=s["status"])
            rows_dict.setdefault(s["row"], []).append(seat)

        rows = [sorted(rows_dict[k], key=lambda s: s.number) for k in sorted(rows_dict.keys())]

        seat_map = SeatMap(
            rows=rows,
            total_rows=len(rows),
            max_seats_per_row=max(len(r) for r in rows) if rows else 0,
        )

        st = Showtime(
            time=showtime.time_display,
            date=showtime.date,
            format="Standard",
            theater_name=showtime.theater_name,
            chain="marcus",
            url=page.url,
        )

        total = sum(len(r) for r in rows)
        avail = sum(1 for r in rows for s in r if s.status == "available")
        logger.info("  %s %s: %d/%d available", showtime.theater_name, showtime.time_display, avail, total)

        return (st, seat_map)

    except Exception as e:
        logger.warning("Marcus seat fetch failed: %s", str(e)[:80])
        return None
    finally:
        await page.close()
