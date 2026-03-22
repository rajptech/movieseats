"""Core agent loop — perceive, decide, execute."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum

import anthropic
from playwright.async_api import Page

from movieseats.agent.prompts import (
    SYSTEM_PROMPT,
    GOAL_SEARCH,
    GOAL_SELECT_SHOWTIME,
    GOAL_READ_SEATS,
)
from movieseats.agent.vision import perceive, extract_seat_map
from movieseats.browser import actions
from movieseats.browser.stealth import dismiss_popups, detect_captcha
from movieseats.seats.models import SearchResult, Showtime
from movieseats.seats.parser import parse_seat_map_response
from movieseats.seats.scorer import find_best_seats
from movieseats.chains.base import ChainConfig
from config import MAX_AGENT_STEPS

logger = logging.getLogger(__name__)


class Phase(Enum):
    SEARCH = "search"
    SELECT_SHOWTIME = "select_showtime"
    READ_SEATS = "read_seats"
    COMPLETE = "complete"


async def run_agent(
    page: Page,
    client: anthropic.AsyncAnthropic,
    chain: ChainConfig,
    zipcode: str,
    movie_name: str,
    num_seats: int = 2,
) -> SearchResult:
    """Run the full agent loop for one theater chain.

    Navigates through 3 phases:
    1. Search — find the movie at theaters near zipcode
    2. Select showtime — pick a showtime
    3. Read seats — extract and score the seat map

    Returns a SearchResult with recommendations or errors.
    """
    result = SearchResult(chain=chain.name)
    history: list[dict] = []
    phase = Phase.SEARCH
    step = 0
    last_actions: list[str] = []  # Track recent actions to detect loops
    last_thought: str = ""  # Track last thought for metadata extraction

    # Phase 1: Navigate to chain and search
    logger.info("=== Starting agent for %s ===", chain.name)
    await actions.go_to_url(page, chain.url)
    await actions.wait_for_stable(page)
    await dismiss_popups(page)

    while step < MAX_AGENT_STEPS and phase != Phase.COMPLETE:
        step += 1

        # Check for CAPTCHA
        if await detect_captcha(page):
            result.errors.append(f"CAPTCHA encountered on {chain.name}")
            logger.warning("CAPTCHA on %s — aborting", chain.name)
            return result

        # Dismiss any new popups
        await dismiss_popups(page)

        # Detect if agent is stuck in a loop
        stuck_hint = ""
        if len(last_actions) >= 3 and len(set(last_actions[-3:])) == 1:
            stuck_hint = (
                "\n\nWARNING: You have repeated the same action 3 times. "
                "Try a completely different approach: use click_text instead of click, "
                "scroll to find the element, or navigate to a different URL."
            )
            logger.warning("Agent appears stuck — injecting hint")

        # Build goal based on current phase
        goal = _get_goal(phase, chain, zipcode, movie_name) + stuck_hint

        # Perceive and decide
        action_data = await perceive(
            page=page,
            client=client,
            system_prompt=SYSTEM_PROMPT,
            goal=goal,
            step=step,
            max_steps=MAX_AGENT_STEPS,
            history=history,
        )

        thought = action_data.get("thought", "")
        action = action_data.get("action", "error")
        params = action_data.get("params", {})
        last_thought = thought  # Save for metadata extraction

        logger.info(
            "Step %d/%d [%s] Action: %s — %s",
            step,
            MAX_AGENT_STEPS,
            phase.value,
            action,
            thought[:80],
        )

        # Track action for loop detection
        action_sig = f"{action}:{str(params)[:50]}"
        last_actions.append(action_sig)
        if len(last_actions) > 5:
            last_actions.pop(0)

        # Execute action (wrapped in try/except so failures don't crash the loop)
        try:
            if action == "click":
                x, y = params.get("x", 0), params.get("y", 0)
                await actions.click(page, x, y, thought)

            elif action == "click_text":
                text = params.get("text", "")
                success = await actions.click_text(page, text, thought)
                if not success:
                    logger.warning("click_text failed for '%s', will retry with different approach", text)

            elif action == "type":
                selector = params.get("selector", "input")
                text = params.get("text", "")
                await actions.type_text(page, selector, text, thought)

            elif action == "press_key":
                key = params.get("key", "Enter")
                await actions.press_key(page, key, thought)

            elif action == "navigate":
                url = params.get("url", "")
                if url:
                    await actions.go_to_url(page, url)

            elif action == "scroll_down":
                await actions.scroll_down(page, params.get("pixels", 300))

            elif action == "scroll_up":
                await actions.scroll_up(page, params.get("pixels", 300))

            elif action == "wait":
                ms = params.get("ms", 2000)
                await asyncio.sleep(ms / 1000)

            elif action == "extract_seats":
                # Run seat map extraction
                logger.info("Extracting seat map...")
                raw_response = await extract_seat_map(
                    page, client, chain.seat_map_hints
                )
                parsed = parse_seat_map_response(raw_response)

                if parsed:
                    # Use metadata from extraction or fallback to agent's thought
                    theater_name = parsed.theater_name or _extract_theater_from_thought(last_thought) or chain.name
                    showtime_text = parsed.showtime or _extract_time_from_thought(last_thought) or ""
                    fmt = parsed.format or "Standard"

                    result.theater_name = theater_name

                    showtime = Showtime(
                        time=showtime_text,
                        date="",
                        format=fmt,
                        theater_name=theater_name,
                        chain=chain.name,
                        url=page.url,
                    )
                    recommendations = find_best_seats(parsed.seat_map, showtime, num_seats)
                    result.recommendations = recommendations
                    logger.info(
                        "Found %d seat recommendations for %s at %s (%s)",
                        len(recommendations), theater_name, showtime_text, fmt,
                    )
                else:
                    result.errors.append("Failed to parse seat map")

                phase = Phase.COMPLETE

            elif action == "done":
                reason = params.get("reason", "")
                logger.info("Phase %s done: %s", phase.value, reason)

                if phase == Phase.SEARCH:
                    phase = Phase.SELECT_SHOWTIME
                    last_actions.clear()
                elif phase == Phase.SELECT_SHOWTIME:
                    phase = Phase.READ_SEATS
                    last_actions.clear()
                elif phase == Phase.READ_SEATS:
                    phase = Phase.COMPLETE

            elif action == "error":
                reason = params.get("reason", "Unknown error")
                result.errors.append(f"{chain.name}: {reason}")
                logger.error("Agent error on %s: %s", chain.name, reason)
                return result

        except Exception as e:
            logger.warning("Action '%s' failed: %s — continuing", action, str(e)[:100])

        # Wait for page to settle after action
        await actions.wait_for_stable(page)

    if step >= MAX_AGENT_STEPS and phase != Phase.COMPLETE:
        result.errors.append(f"Reached max steps ({MAX_AGENT_STEPS}) on {chain.name}")

    return result


def _get_goal(
    phase: Phase,
    chain: ChainConfig,
    zipcode: str,
    movie_name: str,
) -> str:
    """Build the goal string for the current phase."""
    if phase == Phase.SEARCH:
        return GOAL_SEARCH.format(
            chain_url=chain.url,
            movie_name=movie_name,
            zipcode=zipcode,
            chain_hints=chain.search_hints,
        )
    elif phase == Phase.SELECT_SHOWTIME:
        return GOAL_SELECT_SHOWTIME.format(movie_name=movie_name)
    elif phase == Phase.READ_SEATS:
        return GOAL_READ_SEATS.format(seat_hints=chain.seat_map_hints)
    return ""


def _extract_theater_from_thought(thought: str) -> str:
    """Try to extract theater name from agent's thought text."""
    import re
    # Look for patterns like "at Cinemark Frisco Square" or "Cinemark XYZ"
    patterns = [
        r'at (Cinemark[^,.\n]+)',
        r'at (AMC[^,.\n]+)',
        r'at (Regal[^,.\n]+)',
        r'(Cinemark [A-Z][^,.\n]+)',
        r'(AMC [A-Z][^,.\n]+)',
        r'(Regal [A-Z][^,.\n]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, thought, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_time_from_thought(thought: str) -> str:
    """Try to extract showtime from agent's thought text."""
    import re
    match = re.search(r'(\d{1,2}:\d{2}\s*[aApP][mM])', thought)
    if match:
        return match.group(1).strip()
    return ""
