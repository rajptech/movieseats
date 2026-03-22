"""Orchestrator — coordinates multi-chain search."""

from __future__ import annotations

import logging

import anthropic

from movieseats.browser.session import BrowserSession
from movieseats.agent.loop import run_agent
from movieseats.chains.base import ChainConfig
from movieseats.chains.fandango import FANDANGO
from movieseats.chains.amc import AMC
from movieseats.chains.cinemark import CINEMARK
from movieseats.seats.models import SearchResult
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

ALL_CHAINS: dict[str, ChainConfig] = {
    "fandango": FANDANGO,
    "amc": AMC,
    "cinemark": CINEMARK,
}


async def find_best_seats(
    zipcode: str,
    movie_name: str,
    num_seats: int = 2,
    chains: list[str] | None = None,
) -> list[SearchResult]:
    """Search for the best available seats across theater chains.

    Runs chains sequentially — each gets its own browser session.

    Args:
        zipcode: US zipcode to search near.
        movie_name: Name of the movie to find.
        num_seats: Number of adjacent seats needed.
        chains: List of chain names to search. None = all chains.

    Returns:
        List of SearchResult, one per chain attempted.
    """
    chain_names = chains or list(ALL_CHAINS.keys())
    chain_configs = []
    for name in chain_names:
        if name.lower() in ALL_CHAINS:
            chain_configs.append(ALL_CHAINS[name.lower()])
        else:
            logger.warning("Unknown chain: %s (skipping)", name)

    if not chain_configs:
        logger.error("No valid chains to search")
        return []

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    results: list[SearchResult] = []

    for chain in chain_configs:
        logger.info("=" * 60)
        logger.info("Searching %s for '%s' near %s", chain.name, movie_name, zipcode)
        logger.info("=" * 60)

        session = BrowserSession()
        try:
            page = await session.start()
            result = await run_agent(
                page=page,
                client=client,
                chain=chain,
                zipcode=zipcode,
                movie_name=movie_name,
                num_seats=num_seats,
            )
            results.append(result)

            if result.recommendations:
                logger.info(
                    "%s: Found %d recommendations",
                    chain.name,
                    len(result.recommendations),
                )
            if result.errors:
                logger.warning("%s errors: %s", chain.name, result.errors)

        except Exception as e:
            logger.error("Failed to search %s: %s", chain.name, str(e))
            results.append(
                SearchResult(chain=chain.name, errors=[f"Exception: {str(e)}"])
            )
        finally:
            await session.close()

    return results
