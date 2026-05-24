"""Shared helpers for Round 2a (question generation) and Round 2b (answer)
sub-rounds of the Q&A debate protocol (F-10 Session 3).

Per `E:\\Company\\docs\\round-protocol.md` §"Round 2a / Round 2b", each PM
must:
  - Round 2a: read other PMs' Round 1 reasoning and emit up to 3 specific
    questions per addressee.
  - Round 2b: answer only the questions addressed to them with a
    view_changed label.

This module avoids touching the 13 individual PM agent files. Instead it
builds a per-PM prompt from:
  - The agent's methodology Markdown (already loaded by F-1/F-2)
  - The agent's persona description from ANALYST_CONFIG
  - The context (other PMs' Round 1 or addressed questions)

A single LLM call is made per PM per round.
"""

from __future__ import annotations

import logging
from typing import Literal

from src.agents.cio_consensus import _PM_AGENT_KEYS
from src.agents.methodologies import load_methodology
from src.utils.analysts import ANALYST_CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Round 2 firing condition (shared by both round_2a and round_2b agents)
# ---------------------------------------------------------------------------

def should_run_round2(ticker_consensus: dict | None) -> bool:
    """Per round-protocol.md §"Early Consensus":

    | consensus.type | DA verdict        | Round 2 |
    |----------------|-------------------|---------|
    | strong         | proceed           | skip    |
    | strong         | trigger_round2    | run     |
    | soft           | (DA not run)      | run     |
    | split          | (DA not run)      | run     |
    | diverged       | (DA not run)      | run     |
    | insufficient   | (DA not run)      | skip    |
    """
    if not ticker_consensus:
        return False
    ctype = ticker_consensus.get("type")
    if ctype == "insufficient":
        return False
    if ctype == "strong":
        da = ticker_consensus.get("devils_advocate") or {}
        return da.get("recommended_next_step") == "trigger_round2"
    return ctype in {"soft", "split", "diverged"}


# ---------------------------------------------------------------------------
# Agent-key utilities
# ---------------------------------------------------------------------------

def get_round1_pms_for_ticker(analyst_signals: dict, ticker: str) -> list[str]:
    """Return PM agent keys that produced a Round 1 signal for this ticker.

    Sorted for deterministic iteration (important for test reproducibility).
    """
    out: list[str] = []
    for agent_key, ticker_map in (analyst_signals or {}).items():
        if agent_key not in _PM_AGENT_KEYS:
            continue
        if (ticker_map or {}).get(ticker):
            out.append(agent_key)
    return sorted(out)


def agent_key_to_methodology_key(agent_key: str) -> str:
    """warren_buffett_agent -> warren_buffett (for load_methodology)."""
    if agent_key.endswith("_agent"):
        return agent_key[: -len("_agent")]
    return agent_key


def agent_key_to_config_key(agent_key: str) -> str:
    """Same as methodology key (ANALYST_CONFIG keys are without _agent suffix)."""
    return agent_key_to_methodology_key(agent_key)


def persona_description(agent_key: str) -> str:
    """Return the persona description from ANALYST_CONFIG.

    Falls back to a generic label if the agent isn't in the catalog.
    """
    cfg = ANALYST_CONFIG.get(agent_key_to_config_key(agent_key))
    if cfg:
        return f"{cfg['display_name']} — {cfg['description']}. {cfg['investing_style']}"
    return agent_key


def methodology_block(agent_key: str) -> str:
    """Return the deep methodology Markdown for this PM, or "" if not available."""
    return load_methodology(agent_key_to_methodology_key(agent_key))


# ---------------------------------------------------------------------------
# Round 1 formatting (used by both 2a and 2b prompts)
# ---------------------------------------------------------------------------

def format_round1_reasoning(
    analyst_signals: dict,
    ticker: str,
    exclude_agent: str | None = None,
    max_len: int = 600,
) -> str:
    """Render all PMs' Round 1 signals into a compact block.

    Used by Round 2a (each PM sees others' Round 1) and by Round 2b
    (answerer sees their own Round 1 to ground their answers).
    """
    lines: list[str] = []
    for agent_key in get_round1_pms_for_ticker(analyst_signals, ticker):
        if exclude_agent and agent_key == exclude_agent:
            continue
        sig = analyst_signals[agent_key].get(ticker, {})
        signal = sig.get("signal", "?")
        confidence = sig.get("confidence", "?")
        reasoning = (sig.get("reasoning") or "").replace("\n", " ").strip()
        if len(reasoning) > max_len:
            reasoning = reasoning[: max_len - 3] + "..."
        lines.append(
            f"### {agent_key}\n- Signal: {signal} (confidence={confidence})\n- Reasoning: {reasoning}"
        )
    return "\n\n".join(lines) if lines else "(no other Round 1 signals available)"


# ---------------------------------------------------------------------------
# Shared answer label vocabulary (Round 2b uses these strings)
# ---------------------------------------------------------------------------

ViewChangedLabel = Literal["full", "partial", "no", "cannot_answer"]
