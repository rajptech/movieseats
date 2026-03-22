from __future__ import annotations

import logging
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config import HEADLESS, PAGE_LOAD_TIMEOUT_MS

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


class BrowserSession:
    """Manages a Playwright browser session with anti-detection settings."""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    async def start(self) -> Page:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=USER_AGENT,
            locale="en-US",
        )
        self._context.set_default_timeout(PAGE_LOAD_TIMEOUT_MS)
        self.page = await self._context.new_page()

        # Remove webdriver property to reduce detection
        await self.page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        logger.info("Browser session started (headless=%s)", HEADLESS)
        return self.page

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser session closed")
