"""Investor methodology knowledge bases ported from E:\\Company\\docs\\strategies.

Each file under this directory is a Markdown document encoding the deep
investment philosophy of a single investor (4 Filters, historical trades,
failures, JP-specific adaptations, etc). Loaded once at module import by
agents and prepended to their LLM system prompt to lift judgement quality.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_METHODOLOGY_DIR = Path(__file__).parent


@lru_cache(maxsize=32)
def load_methodology(agent_name: str) -> str:
    """Load the methodology Markdown for an agent (e.g. 'warren_buffett').

    Returns empty string if the file is missing — agents must accept this
    gracefully and fall back to their built-in compact prompt.
    """
    path = _METHODOLOGY_DIR / f"{agent_name}.md"
    try:
        text = path.read_text(encoding="utf-8")
        logger.info("Loaded methodology: %s (%d chars)", path.name, len(text))
        return text
    except FileNotFoundError:
        logger.warning("Methodology not found: %s — agent will use compact prompt only", path)
        return ""
    except Exception as e:
        logger.warning("Failed to load methodology %s: %s", path, e)
        return ""
