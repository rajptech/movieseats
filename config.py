import os
from dotenv import load_dotenv

load_dotenv()

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Models — use latest as of March 2026
MODEL_FAST = "claude-haiku-4-5-20251001"    # Intent parsing, recommendations ($1/$5 MTok)
MODEL_SMART = "claude-sonnet-4-6"           # Web search, complex reasoning ($3/$15 MTok)

# Browser
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
PAGE_LOAD_TIMEOUT_MS = 15000
SCREENSHOT_DELAY_MS = 1500

# Agent loop (legacy v1)
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
