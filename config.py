import os
from dotenv import load_dotenv

load_dotenv()

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Browser
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
PAGE_LOAD_TIMEOUT_MS = 15000
SCREENSHOT_DELAY_MS = 1500

# Agent loop
MAX_AGENT_STEPS = 35

# Seat scoring weights
WEIGHT_CENTER = 0.40
WEIGHT_ROW = 0.35
WEIGHT_ADJACENCY = 0.25
IDEAL_ROW_RATIO = 0.65  # 65% back from screen is ideal

# Human-like behavior
MIN_ACTION_DELAY_MS = 800
MAX_ACTION_DELAY_MS = 2500

# Chain entry points
CHAINS = {
    "fandango": "https://www.fandango.com",
    "amc": "https://www.amctheatres.com",
    "cinemark": "https://www.cinemark.com",
}
