"""India theater fetcher via District.in (Paytm Movies).

Covers ALL Indian chains: PVR, INOX, Cinepolis, Miraj, independent theaters.
Uses District.in's JSON seat API — no DOM scraping needed for seats.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from playwright.async_api import BrowserContext, Page

from movieseats.seats.models import Seat, SeatMap, Showtime

logger = logging.getLogger(__name__)

DISTRICT_BASE = "https://www.district.in"

# Major Indian cities and their District.in URL slugs
INDIA_CITIES = {
    "hyderabad": "hyderabad", "mumbai": "mumbai", "delhi": "delhi-ncr",
    "bangalore": "bengaluru", "bengaluru": "bengaluru", "chennai": "chennai",
    "kolkata": "kolkata", "pune": "pune", "ahmedabad": "ahmedabad",
    "jaipur": "jaipur", "lucknow": "lucknow", "kochi": "kochi",
    "chandigarh": "chandigarh", "indore": "indore", "bhopal": "bhopal",
    "vizag": "visakhapatnam", "visakhapatnam": "visakhapatnam",
    "noida": "delhi-ncr", "gurgaon": "delhi-ncr", "gurugram": "delhi-ncr",
    "new delhi": "delhi-ncr", "navi mumbai": "mumbai", "thane": "mumbai",
    "gachibowli": "hyderabad", "hitech city": "hyderabad",
    "banjara hills": "hyderabad", "secunderabad": "hyderabad",
    "kukatpally": "hyderabad", "ameerpet": "hyderabad",
}


@dataclass
class IndiaShowtime:
    theater_name: str
    time_display: str
    format: str  # "DOLBY ATMOS", "IMAX", "4DX", "Standard"
    distance: str  # "1.1 km away"
    session_id: str  # for seat API
    availability: str  # "Available", "Filling fast", "Almost full"
    url: str


async def find_india_theaters(
    context: BrowserContext,
    city: str,
    movie_name: str,
    max_per_theater: int = 3,
) -> list[tuple[str, list[IndiaShowtime]]]:
    """Find theaters in an Indian city showing a movie.

    Returns list of (theater_name, showtimes).
    """
    page = await context.new_page()
    results: list[tuple[str, list[IndiaShowtime]]] = []

    try:
        # Resolve city to District.in slug
        city_slug = INDIA_CITIES.get(city.lower().strip(), city.lower().strip().replace(" ", "-"))

        # Step 1: Find movie slug via web search on District.in
        movie_slug = await _find_india_movie_slug(page, movie_name, city_slug)
        if not movie_slug:
            logger.error("Movie not found on District.in: %s in %s", movie_name, city)
            return []

        # Step 2: Go to movie page
        url = f"{DISTRICT_BASE}/movies/{movie_slug}-in-{city_slug}-MV"
        # Try the URL, if it fails try searching
        await page.goto(f"{DISTRICT_BASE}/movies/{movie_slug}", wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)

        # Check if we're on the right page
        page_text = await page.inner_text("body")
        if "Select Location" in page_text and len(page_text) < 500:
            # Need to select city first
            logger.info("Selecting city: %s", city_slug)
            try:
                city_btn = page.get_by_text(city.title(), exact=False)
                if await city_btn.count() > 0:
                    await city_btn.first.click(force=True, timeout=3000)
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

        # Step 3: Extract all theaters and showtimes
        data = await page.evaluate(r"""() => {
            const results = [];
            const text = document.body.innerText;
            const lines = text.split('\n').map(l => l.trim()).filter(l => l);

            let currentTheater = '';
            let currentDistance = '';

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];

                // Detect theater names (PVR, INOX, Cinepolis, etc.)
                if (line.match(/(PVR|INOX|Cinepolis|Cinépolis|Miraj|Asian|Carnival|Roongta|AMB |SPI |Fun |Gold )/i) && line.length < 80) {
                    currentTheater = line;
                    // Check next line for distance
                    if (i + 1 < lines.length && lines[i + 1].match(/\d+\.?\d*\s*km/i)) {
                        currentDistance = lines[i + 1];
                    }
                }

                // Detect showtimes (HH:MM AM/PM pattern)
                const timeMatch = line.match(/^(\d{1,2}:\d{2}\s*[AP]M)$/i);
                if (timeMatch && currentTheater) {
                    // Check for format on next line
                    let fmt = 'Standard';
                    if (i + 1 < lines.length) {
                        const next = lines[i + 1];
                        if (next.includes('DOLBY') || next.includes('Atmos')) fmt = 'Dolby Atmos';
                        else if (next.includes('IMAX')) fmt = 'IMAX';
                        else if (next.includes('4DX')) fmt = '4DX';
                        else if (next.includes('3D')) fmt = '3D';
                    }

                    results.push({
                        theater: currentTheater,
                        time: timeMatch[1],
                        format: fmt,
                        distance: currentDistance,
                    });
                }
            }

            return results;
        }""")

        # Also get seat layout links
        seat_links = await page.evaluate(r"""() => {
            const links = document.querySelectorAll('a[href*="seat-layout"]');
            return Array.from(links).map(a => ({
                href: a.href,
                text: a.innerText.trim().substring(0, 30),
            }));
        }""")

        logger.info("Found %d showtimes and %d seat links in %s", len(data), len(seat_links), city)

        # Group by theater
        theater_map: dict[str, list[IndiaShowtime]] = {}
        for i, item in enumerate(data):
            name = item["theater"]
            if name not in theater_map:
                theater_map[name] = []

            # Find matching seat link
            seat_url = ""
            if i < len(seat_links):
                seat_url = seat_links[i].get("href", "")

            theater_map[name].append(IndiaShowtime(
                theater_name=name,
                time_display=item["time"],
                format=item["format"],
                distance=item.get("distance", ""),
                session_id="",
                availability="Available",
                url=seat_url,
            ))

        # Limit per theater
        for name, showtimes in theater_map.items():
            picked = showtimes[:max_per_theater]
            results.append((name, picked))

        logger.info("Found %d theaters for %s in %s", len(results), movie_name, city)

    except Exception as e:
        logger.error("India theater discovery failed: %s", e)
    finally:
        await page.close()

    return results


async def _find_india_movie_slug(page: Page, movie_name: str, city_slug: str) -> str | None:
    """Find movie slug on District.in."""
    try:
        await page.goto(f"{DISTRICT_BASE}/movies/{city_slug}", wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        slug = await page.evaluate(r"""(name) => {
            const links = document.querySelectorAll('a[href*="/movies/"]');
            const nameLower = name.toLowerCase();
            const words = nameLower.split(/\s+/).filter(w => w.length > 2);

            for (const link of links) {
                const text = link.innerText.toLowerCase();
                const href = link.href.toLowerCase();
                if (text.includes(nameLower) || href.includes(nameLower.replace(/\s+/g, '-'))) {
                    const match = link.href.match(/\/movies\/([^?]+)/);
                    if (match) return match[1];
                }
            }
            // Word match
            for (const link of links) {
                const text = (link.innerText + ' ' + link.href).toLowerCase();
                for (const word of words) {
                    if (word.length > 3 && text.includes(word)) {
                        const match = link.href.match(/\/movies\/([^?]+)/);
                        if (match && !match[1].includes('explore')) return match[1];
                    }
                }
            }
            return null;
        }""", movie_name)

        if slug:
            logger.info("Found India movie slug: %s", slug)
        return slug
    except Exception as e:
        logger.error("India movie slug search failed: %s", e)
        return None


async def fetch_india_seat_map(
    context: BrowserContext,
    showtime: IndiaShowtime,
    movie_name: str,
    city: str,
) -> tuple[Showtime, SeatMap] | None:
    """Fetch seat map for an Indian showtime via District.in's JSON API."""
    page = await context.new_page()

    try:
        seat_json = [None]

        async def on_response(response):
            if "select-seat" in response.url or "seat-layout" in response.url.lower():
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        seat_json[0] = await response.json()
                    except Exception:
                        pass

        page.on("response", on_response)

        if showtime.url:
            await page.goto(showtime.url, wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(3000)

        if not seat_json[0]:
            # Try navigating through the movie page
            city_slug = INDIA_CITIES.get(city.lower().strip(), city.lower().strip())
            movie_slug = movie_name.lower().replace(" ", "-")
            await page.goto(
                f"{DISTRICT_BASE}/movies/{movie_slug}-in-{city_slug}",
                wait_until="networkidle", timeout=15000
            )
            await page.wait_for_timeout(2000)

            # Click the showtime
            try:
                await page.evaluate('document.querySelector(".BottomSheet_container__4XCDW")?.remove()')
            except Exception:
                pass

            try:
                await page.get_by_text(showtime.time_display, exact=True).first.click(force=True, timeout=5000)
                await page.wait_for_timeout(5000)
            except Exception:
                pass

        if not seat_json[0]:
            logger.warning("No seat data for %s %s", showtime.theater_name, showtime.time_display)
            await page.close()
            return None

        # Parse the JSON seat data
        data = seat_json[0]
        seat_layout = data.get("seatLayout", {})
        col_areas = seat_layout.get("colAreas", {})
        obj_areas = col_areas.get("objArea", [])

        rows_list: list[list[Seat]] = []
        for area in obj_areas:
            for row_data in area.get("objRow", []):
                row_letter = row_data.get("PhyRowId", "")
                if not row_letter or len(row_letter) > 2:
                    continue

                row_seats: list[Seat] = []
                for seat_data in row_data.get("objSeat", []):
                    seat_num = seat_data.get("SeatNum", 0)
                    if not seat_num:
                        continue

                    # Status: 0 = available, 1 = sold/booked
                    status_code = seat_data.get("SeatStatus", 1)
                    seat_type = seat_data.get("SeatType", "")

                    if seat_type in ("Wheelchair", "Companion"):
                        status = "wheelchair"
                    elif status_code == 0:
                        status = "available"
                    else:
                        status = "taken"

                    row_seats.append(Seat(
                        row=row_letter,
                        number=seat_num,
                        status=status,
                    ))

                if row_seats:
                    # Filter out wheelchair-only rows
                    regular = [s for s in row_seats if s.status in ("available", "taken")]
                    if regular:
                        rows_list.append(sorted(row_seats, key=lambda s: s.number))

        if not rows_list:
            await page.close()
            return None

        seat_map = SeatMap(
            rows=rows_list,
            total_rows=len(rows_list),
            max_seats_per_row=max(len(r) for r in rows_list),
        )

        # Get price from API response
        price = 0
        try:
            ticket_types = data.get("ticketTypes", [])
            if ticket_types:
                price = ticket_types[0].get("price", 0)
        except Exception:
            pass

        st = Showtime(
            time=showtime.time_display,
            date="",
            format=showtime.format,
            price=float(price),
            theater_name=showtime.theater_name,
            chain="district.in",
            url=showtime.url or page.url,
        )

        total = sum(len(r) for r in rows_list)
        avail = sum(1 for r in rows_list for s in r if s.status == "available")
        logger.info("  %s %s: %d/%d available", showtime.theater_name, showtime.time_display, avail, total)

        await page.close()
        return (st, seat_map)

    except Exception as e:
        logger.warning("India seat fetch failed: %s", str(e)[:80])
        await page.close()
        return None
