"""Fast theater + showtime discovery via Playwright."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)

CINEMARK_BASE = "https://www.cinemark.com"


@dataclass
class ShowtimeInfo:
    theater_id: str
    showtime_id: str
    movie_id: str
    showtime_dt: str
    time_display: str
    format: str
    url: str


@dataclass
class TheaterInfo:
    name: str
    slug: str = ""
    showtimes: list[ShowtimeInfo] = field(default_factory=list)


async def find_theaters_and_showtimes(
    context: BrowserContext,
    zipcode: str,
    movie_name: str,
    date_text: str = "",
    max_per_theater: int = 5,
    time_pref: str = "evening",
) -> list[TheaterInfo]:
    """Find Cinemark theaters near zipcode showing a movie.

    Args:
        context: Playwright browser context (shared with seat fetcher).
        zipcode: US zipcode.
        movie_name: Movie name to search.
        date_text: Date to select (e.g., "3/22"). Empty = today.
        max_per_theater: Max showtimes to check per theater (for speed).
    """
    page = await context.new_page()
    theaters: list[TheaterInfo] = []

    try:
        # Step 1: Find movie slug
        await page.goto(f"{CINEMARK_BASE}/movies", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(1500)

        movie_slug = await page.evaluate("""(name) => {
            const links = document.querySelectorAll('a[href*="/movies/"]');
            const nameLower = name.toLowerCase();
            const words = nameLower.split(/\\s+/);
            for (const link of links) {
                const text = link.innerText.toLowerCase();
                if (text.includes(nameLower)) {
                    const match = link.href.match(/\\/movies\\/([^/?]+)/);
                    if (match) return match[1];
                }
            }
            for (const link of links) {
                const text = link.innerText.toLowerCase();
                for (const word of words) {
                    if (word.length > 3 && text.includes(word)) {
                        const match = link.href.match(/\\/movies\\/([^/?]+)/);
                        if (match) return match[1];
                    }
                }
            }
            return null;
        }""", movie_name)

        if not movie_slug:
            logger.error("Movie not found: %s", movie_name)
            return []

        logger.info("Found movie: %s", movie_slug)

        # Step 2: Go to movie page
        await page.goto(f"{CINEMARK_BASE}/movies/{movie_slug}", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(1500)

        # Step 3: Enter zipcode
        try:
            await page.get_by_text("ZIP Code").first.click(timeout=3000)
            await page.wait_for_timeout(300)
        except Exception:
            pass

        zip_input = page.locator('input[type="tel"]')
        if await zip_input.count() > 0:
            await zip_input.first.click()
            await zip_input.first.fill(zipcode)
            await zip_input.first.press("Enter")
            await page.wait_for_timeout(2500)
        else:
            logger.error("Could not find zipcode input")
            return []

        # Step 4: Select date if specified
        if date_text:
            clicked = False
            # Try clicking the date directly
            try:
                date_el = page.get_by_text(date_text, exact=True)
                if await date_el.count() > 0:
                    await date_el.first.click(timeout=3000)
                    clicked = True
            except Exception:
                pass

            # If not found, scroll the date carousel right and try again
            if not clicked:
                for _ in range(3):
                    try:
                        next_arrow = page.locator(".carousel__arrow--next, .slick-next, [class*='showdate'] ~ [class*='next']")
                        if await next_arrow.count() > 0:
                            await next_arrow.first.click(force=True, timeout=2000)
                            await page.wait_for_timeout(500)
                        date_el = page.get_by_text(date_text, exact=True)
                        if await date_el.count() > 0:
                            await date_el.first.click(force=True, timeout=3000)
                            clicked = True
                            break
                    except Exception:
                        continue

            if clicked:
                await page.wait_for_timeout(2500)
            else:
                logger.warning("Could not click date: %s", date_text)

        # Step 5: Extract all showtime links grouped by theater
        data = await page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('a[href*="TicketSeatMap"]').forEach(a => {
                let parent = a.parentElement;
                let theater = '';
                while (parent && !theater) {
                    const h = parent.querySelector('h3, h4');
                    if (h) theater = h.innerText.trim();
                    parent = parent.parentElement;
                }
                results.push({
                    theater: theater || 'Unknown Theater',
                    time: a.innerText.trim(),
                    href: a.href,
                });
            });
            return results;
        }""")

        # Group by theater
        theater_map: dict[str, TheaterInfo] = {}
        for item in data:
            name = item["theater"]
            if name not in theater_map:
                theater_map[name] = TheaterInfo(name=name)

            parsed = _parse_ticket_url(item["href"])
            if not parsed:
                continue

            theater_map[name].showtimes.append(
                ShowtimeInfo(
                    theater_id=parsed["theater_id"],
                    showtime_id=parsed["showtime_id"],
                    movie_id=parsed["movie_id"],
                    showtime_dt=parsed["showtime_dt"],
                    time_display=item["time"],
                    format=_detect_format(name),
                    url=item["href"],
                )
            )

        # Limit showtimes per theater (pick spread: morning, afternoon, evening)
        for theater in theater_map.values():
            if len(theater.showtimes) > max_per_theater:
                theater.showtimes = _pick_best_showtimes(theater.showtimes, max_per_theater, time_pref)

        theaters = list(theater_map.values())
        total = sum(len(t.showtimes) for t in theaters)
        logger.info("Found %d theaters with %d showtimes (capped)", len(theaters), total)

    except Exception as e:
        logger.error("Theater discovery failed: %s", e)
    finally:
        await page.close()

    return theaters


def _parse_ticket_url(url: str) -> dict | None:
    match = re.search(
        r'TheaterId=(\d+)&ShowtimeId=(\d+)&CinemarkMovieId=(\d+).*?Showtime=([^&"]+)',
        url,
    )
    if match:
        return {
            "theater_id": match.group(1),
            "showtime_id": match.group(2),
            "movie_id": match.group(3),
            "showtime_dt": match.group(4),
        }
    return None


def _detect_format(theater_name: str) -> str:
    name = theater_name.lower()
    if "imax" in name:
        return "IMAX"
    if "xd" in name:
        return "XD"
    if "screenx" in name:
        return "SCREENX"
    if "d-box" in name:
        return "D-BOX"
    return "Standard"


def _pick_best_showtimes(showtimes: list[ShowtimeInfo], n: int, time_pref: str = "evening") -> list[ShowtimeInfo]:
    """Pick a spread of showtimes based on time preference."""
    def _hour(st: ShowtimeInfo) -> int:
        m = re.search(r'T(\d{2})', st.showtime_dt)
        return int(m.group(1)) if m else 12

    morning = [s for s in showtimes if _hour(s) < 12]
    afternoon = [s for s in showtimes if 12 <= _hour(s) < 18]
    evening = [s for s in showtimes if 18 <= _hour(s) <= 21]
    late = [s for s in showtimes if _hour(s) > 21]

    if time_pref == "morning":
        order = [morning, afternoon, evening, late]
    elif time_pref == "afternoon":
        order = [afternoon, morning, evening, late]
    elif time_pref == "all":
        # Return spread: 1 morning, 1 afternoon, 1 evening
        picked = []
        for pool in [morning, afternoon, evening]:
            if pool:
                picked.append(pool[0])
        return picked[:n] if picked else showtimes[:n]
    else:  # evening (default)
        order = [evening, afternoon, morning, late]

    picked = []
    for pool in order:
        for s in pool:
            if len(picked) >= n:
                break
            picked.append(s)
    return picked[:n]
