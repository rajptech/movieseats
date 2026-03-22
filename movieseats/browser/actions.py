from __future__ import annotations

import asyncio
import logging
import random

from playwright.async_api import Page

from config import MIN_ACTION_DELAY_MS, MAX_ACTION_DELAY_MS, SCREENSHOT_DELAY_MS

logger = logging.getLogger(__name__)


async def _human_delay():
    """Random delay to mimic human interaction speed."""
    delay = random.randint(MIN_ACTION_DELAY_MS, MAX_ACTION_DELAY_MS) / 1000
    await asyncio.sleep(delay)


async def click(page: Page, x: int, y: int, description: str = "") -> None:
    """Click at coordinates with human-like delay."""
    logger.info("Click (%d, %d) %s", x, y, description)
    await _human_delay()
    await page.mouse.click(x, y)
    await asyncio.sleep(SCREENSHOT_DELAY_MS / 1000)


async def click_selector(page: Page, selector: str, description: str = "") -> None:
    """Click an element by CSS selector."""
    logger.info("Click selector '%s' %s", selector, description)
    await _human_delay()
    await page.click(selector, timeout=5000)
    await asyncio.sleep(SCREENSHOT_DELAY_MS / 1000)


async def type_text(page: Page, selector: str, text: str, description: str = "") -> bool:
    """Type text into an element with human-like keystroke delays. Returns True on success."""
    logger.info("Type '%s' into '%s' %s", text, selector, description)
    await _human_delay()

    # Try multiple strategies to find the input
    typed = False
    strategies = [
        selector,
        f"input[placeholder*='{text[:10]}' i]",
        "input:visible",
        "input[type='text']:visible",
        "input[type='search']:visible",
        "[contenteditable='true']:visible",
    ]

    for strat in strategies:
        try:
            locator = page.locator(strat).first
            if await locator.count() > 0:
                await locator.click(timeout=3000)
                await locator.fill("", timeout=3000)
                for char in text:
                    await page.keyboard.type(char, delay=random.randint(50, 150))
                typed = True
                break
        except Exception:
            continue

    if not typed:
        # Last resort: just type into whatever is focused
        try:
            logger.warning("Falling back to keyboard typing without selector")
            for char in text:
                await page.keyboard.type(char, delay=random.randint(50, 150))
            typed = True
        except Exception as e:
            logger.error("All type strategies failed: %s", e)

    await asyncio.sleep(SCREENSHOT_DELAY_MS / 1000)
    return typed


async def press_key(page: Page, key: str, description: str = "") -> None:
    """Press a keyboard key (Enter, Tab, Escape, etc.)."""
    logger.info("Press key '%s' %s", key, description)
    await _human_delay()
    await page.keyboard.press(key)
    await asyncio.sleep(SCREENSHOT_DELAY_MS / 1000)


async def click_text(page: Page, text: str, description: str = "") -> bool:
    """Click an element by its visible text. Returns True if found and clicked."""
    logger.info("Click text '%s' %s", text, description)
    await _human_delay()
    try:
        # Try exact text match first
        locator = page.get_by_text(text, exact=True)
        if await locator.count() > 0:
            await locator.first.click(timeout=5000)
            await asyncio.sleep(SCREENSHOT_DELAY_MS / 1000)
            return True
        # Try partial match
        locator = page.get_by_text(text, exact=False)
        if await locator.count() > 0:
            await locator.first.click(timeout=5000)
            await asyncio.sleep(SCREENSHOT_DELAY_MS / 1000)
            return True
        # Try role-based (button/link with that name)
        for role in ["button", "link"]:
            locator = page.get_by_role(role, name=text)
            if await locator.count() > 0:
                await locator.first.click(timeout=5000)
                await asyncio.sleep(SCREENSHOT_DELAY_MS / 1000)
                return True
        logger.warning("Could not find element with text '%s'", text)
        return False
    except Exception as e:
        logger.warning("click_text failed for '%s': %s", text, e)
        return False


async def scroll_down(page: Page, pixels: int = 300) -> None:
    """Scroll down the page."""
    logger.info("Scroll down %d px", pixels)
    await page.mouse.wheel(0, pixels)
    await asyncio.sleep(0.5)


async def scroll_up(page: Page, pixels: int = 300) -> None:
    """Scroll up the page."""
    logger.info("Scroll up %d px", pixels)
    await page.mouse.wheel(0, -pixels)
    await asyncio.sleep(0.5)


async def go_to_url(page: Page, url: str) -> None:
    """Navigate to a URL and wait for load."""
    logger.info("Navigate to %s", url)
    await page.goto(url, wait_until="domcontentloaded")
    await asyncio.sleep(SCREENSHOT_DELAY_MS / 1000)


async def take_screenshot(page: Page, full_page: bool = False) -> bytes:
    """Capture a screenshot as PNG bytes."""
    return await page.screenshot(type="png", full_page=full_page)


async def get_page_text(page: Page, max_chars: int = 4000) -> str:
    """Get visible text content from the page, truncated."""
    try:
        text = await page.inner_text("body", timeout=3000)
        return text[:max_chars]
    except Exception:
        return ""


async def wait_for_stable(page: Page, timeout_ms: int = 5000) -> None:
    """Wait for network to be idle (no pending requests)."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass  # timeout is acceptable, page may have persistent connections
