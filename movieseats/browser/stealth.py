from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Common popup/cookie banner selectors across theater sites
POPUP_SELECTORS = [
    "#onetrust-accept-btn-handler",  # OneTrust cookie consent (AMC, Cinemark)
    "[data-testid='cookie-banner'] button",
    "button[class*='cookie']",
    ".ab-close-button",  # Braze in-app messages
    "[aria-label='Close']",
    "[aria-label='close']",
    "button[class*='modal-close']",
    "button[class*='dismiss']",
    "[data-testid='close-button']",
    ".email-signup-close",
    "#close-dialog",
]


async def dismiss_popups(page: Page) -> int:
    """Try to dismiss common popups and overlays. Returns count dismissed."""
    dismissed = 0
    for selector in POPUP_SELECTORS:
        try:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                await element.click()
                dismissed += 1
                logger.info("Dismissed popup: %s", selector)
                await asyncio.sleep(0.3)
        except Exception:
            continue
    return dismissed


async def detect_captcha(page: Page) -> bool:
    """Check if the page has a CAPTCHA challenge."""
    captcha_indicators = [
        "iframe[src*='recaptcha']",
        "iframe[src*='captcha']",
        "#captcha",
        ".captcha",
        "[class*='captcha']",
        "iframe[src*='hcaptcha']",
    ]
    for selector in captcha_indicators:
        try:
            element = await page.query_selector(selector)
            if element:
                logger.warning("CAPTCHA detected: %s", selector)
                return True
        except Exception:
            continue
    return False
