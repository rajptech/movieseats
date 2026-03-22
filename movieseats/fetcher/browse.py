"""Browse all movies playing at nearby Cinemark theaters."""

from __future__ import annotations

import logging

from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)

CINEMARK_BASE = "https://www.cinemark.com"

# Words that are NOT movie names
JUNK_WORDS = {
    "get tickets", "buy tickets", "now playing", "coming soon", "advance tickets",
    "featured", "cinemark xd", "cinearts", "movies", "theatres", "gift cards",
    "food & drink", "movie rewards", "sign in", "search", "location",
    "zip search", "more info", "learn more", "see all", "view all",
    "d-box", "screenx", "imax", "3d", "standard", "premium",
}


async def browse_movies_near(
    context: BrowserContext,
    zipcode: str,
) -> list[dict]:
    """Get all movies playing at Cinemark theaters near a zipcode."""
    page = await context.new_page()
    movies = []

    try:
        await page.goto(f"{CINEMARK_BASE}/movies", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        # Set location
        try:
            await page.get_by_text("ZIP Search").first.click(timeout=3000)
            await page.wait_for_timeout(300)
        except Exception:
            try:
                await page.get_by_text("Location").first.click(timeout=3000)
                await page.wait_for_timeout(300)
            except Exception:
                pass

        zip_input = page.locator('input[type="tel"], input[placeholder*="ZIP" i]')
        if await zip_input.count() > 0:
            await zip_input.first.click()
            await zip_input.first.fill(zipcode)
            await zip_input.first.press("Enter")
            await page.wait_for_timeout(2500)

        # Extract movies — look for movie title elements specifically
        movie_data = await page.evaluate(r"""() => {
            const results = [];
            const seen = new Set();

            // Strategy 1: Find movie cards/blocks with title + link
            const movieCards = document.querySelectorAll(
                '.movieBlock, .movie-card, [class*="movie-item"], [class*="movieItem"]'
            );
            movieCards.forEach(card => {
                const titleEl = card.querySelector('h3, h4, [class*="title"], [class*="Title"]');
                const linkEl = card.querySelector('a[href*="/movies/"]');
                if (titleEl && linkEl) {
                    const name = titleEl.innerText.trim();
                    const match = linkEl.href.match(/\/movies\/([^/?]+)/);
                    if (match && name.length > 2 && name.length < 80) {
                        const slug = match[1];
                        if (!seen.has(slug) && slug !== 'book-now') {
                            seen.add(slug);
                            results.push({ name, slug });
                        }
                    }
                }
            });

            // Strategy 2: If no cards found, parse movie links more carefully
            if (results.length === 0) {
                const links = document.querySelectorAll('a[href*="/movies/"]');
                links.forEach(a => {
                    const match = a.href.match(/\/movies\/([^/?]+)/);
                    if (!match) return;
                    const slug = match[1];
                    if (slug === 'book-now' || slug === 'movies' || seen.has(slug)) return;

                    // Get the text — prefer image alt text or heading text over link text
                    let name = '';
                    const img = a.querySelector('img');
                    if (img && img.alt && img.alt.length > 3) {
                        name = img.alt;
                    }
                    if (!name) {
                        const heading = a.querySelector('h3, h4, h5, [class*="title"]');
                        if (heading) name = heading.innerText.trim();
                    }
                    if (!name) {
                        // Use link text but only if it looks like a movie name
                        const text = a.innerText.trim();
                        if (text.length > 3 && text.length < 80 && !/^(get|buy|see|view|more|sign|log)/i.test(text)) {
                            name = text;
                        }
                    }

                    if (name && !seen.has(slug)) {
                        seen.add(slug);
                        results.push({ name, slug });
                    }
                });
            }

            return results;
        }""")

        # Filter and clean
        for m in movie_data:
            name = m["name"].strip()

            # Strip "Poster for " prefix from image alt text
            if name.lower().startswith("poster for "):
                name = name[11:].strip()

            # Strip other common prefixes
            for prefix in ["Advance Tickets ", "Get Tickets for ", "Buy Tickets for "]:
                if name.startswith(prefix):
                    name = name[len(prefix):].strip()

            name_lower = name.lower()
            if name_lower in JUNK_WORDS:
                continue
            if any(name_lower.startswith(j) for j in ["get ", "buy ", "see ", "view ", "sign "]):
                continue
            if len(name) < 3:
                continue

            m["name"] = name
            movies.append(m)

        logger.info("Found %d movies near %s", len(movies), zipcode)

    except Exception as e:
        logger.error("Browse movies failed: %s", e)
    finally:
        await page.close()

    return movies
