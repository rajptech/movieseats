"""Claude vision module — the agent's eyes."""

from __future__ import annotations

import base64
import json
import logging

import anthropic
from playwright.async_api import Page

from movieseats.browser.actions import take_screenshot, get_page_text
from movieseats.agent.prompts import SEAT_MAP_EXTRACTION_PROMPT
from config import CLAUDE_MODEL

logger = logging.getLogger(__name__)


async def perceive(
    page: Page,
    client: anthropic.AsyncAnthropic,
    system_prompt: str,
    goal: str,
    step: int,
    max_steps: int,
    history: list[dict],
) -> dict:
    """Take a screenshot, send to Claude with context, get action decision.

    Returns parsed JSON with 'thought', 'action', and 'params'.
    """
    screenshot_bytes = await take_screenshot(page)
    page_text = await get_page_text(page)
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    current_url = page.url

    # Build the user message with screenshot + context
    user_content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": screenshot_b64,
            },
        },
        {
            "type": "text",
            "text": (
                f"Step {step}/{max_steps}\n"
                f"Current URL: {current_url}\n"
                f"Goal: {goal}\n\n"
                f"Page text (first 4000 chars):\n{page_text}\n\n"
                "What action should I take next? Respond with JSON only."
            ),
        },
    ]

    # Keep conversation history manageable — last 4 exchanges
    trimmed_history = _trim_history(history)

    messages = trimmed_history + [{"role": "user", "content": user_content}]

    response = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )

    response_text = response.content[0].text
    logger.debug("Claude response: %s", response_text)

    # Parse JSON from response
    action_data = _parse_json_response(response_text)

    # Add this exchange to history (replace image with text summary to save tokens)
    history.append(
        {
            "role": "user",
            "content": f"[Screenshot at step {step}, URL: {current_url}]",
        }
    )
    history.append({"role": "assistant", "content": response_text})

    return action_data


async def extract_seat_map(
    page: Page,
    client: anthropic.AsyncAnthropic,
    seat_hints: str = "",
) -> str:
    """Send screenshot to Claude specifically for seat map extraction.

    Takes a full-page screenshot to capture the entire seat map.
    Returns the raw JSON string for parsing by seats/parser.py.
    """
    # Take FULL PAGE screenshot to capture entire seat map
    screenshot_bytes = await take_screenshot(page, full_page=True)
    page_text = await get_page_text(page, max_chars=8000)
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    prompt_text = SEAT_MAP_EXTRACTION_PROMPT
    if seat_hints:
        prompt_text += f"\n\nAdditional hints: {seat_hints}"
    prompt_text += f"\n\nPage text:\n{page_text}"

    response = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
    )

    return response.content[0].text


def _trim_history(history: list[dict], max_exchanges: int = 4) -> list[dict]:
    """Keep only the last N user/assistant exchanges to manage context size."""
    if len(history) <= max_exchanges * 2:
        return history.copy()
    return history[-(max_exchanges * 2) :]


def _parse_json_response(text: str) -> dict:
    """Extract JSON from Claude's response, handling markdown wrapping."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response as JSON: %s", text[:200])
        return {"thought": "Failed to parse response", "action": "error", "params": {"reason": "Invalid JSON response"}}
