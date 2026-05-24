"""CIO Consensus Detection — Round 1.5 of the Q&A debate protocol.

Sits between the parallel Round-1 PM agents and the Risk Manager. Classifies
the multi-PM agreement state per ticker so the shareholder can quickly tell
whether the recommendation is high-trust (unanimous + confident) or whether
it warrants more debate (split decisions, low confidence).

Classification follows E:\\Company\\docs\\round-protocol.md and cio.md:

| Type        | Definition                                                   |
|-------------|--------------------------------------------------------------|
| strong      | All PMs same signal AND all confidence ≥ 50 (mid+)           |
| soft        | All PMs same signal BUT at least one confidence < 50         |
| split       | Two distinct signals among PMs (e.g. 2 neutral + 1 bearish)  |
| diverged    | Three distinct signals (1 bull + 1 neutral + 1 bear)         |
| insufficient| Fewer than 2 PMs ran on the ticker                           |

This module is read-only with respect to per-agent signals; it only writes
the aggregated consensus dict to state.data.consensus[ticker].

The presence of this node enables future Round 2a/2b/3 and Devil's Advocate
sub-rounds by giving them a single consensus contract to consume.
"""

from __future__ import annotations

import logging
from typing import Iterable

from src.graph.state import AgentState, show_agent_reasoning
from src.utils.progress import progress

logger = logging.getLogger(__name__)


# Wall Street legend "Portfolio Manager"-class agents (vs. specialist analysts).
# Specialist nodes like fundamentals_analyst, sentiment_analyst, technicals,
# valuation, news_sentiment, growth are excluded — they are inputs, not voters.
_PM_AGENT_KEYS: frozenset[str] = frozenset({
    "warren_buffett_agent",
    "charlie_munger_agent",
    "ben_graham_agent",
    "bill_ackman_agent",
    "michael_burry_agent",
    "mohnish_pabrai_agent",
    "nassim_taleb_agent",
    "peter_lynch_agent",
    "phil_fisher_agent",
    "stanley_druckenmiller_agent",
    "aswath_damodaran_agent",
    "rakesh_jhunjhunwala_agent",
    "cathie_wood_agent",
})


CONFIDENCE_FLOOR = 50  # below this is treated as "low" per round-protocol.md

# Acceptable signal labels for a PM vote. Anything else (typos, hallucinated
# strings like "upwards" / "neutral-ish" / None) is excluded from the vote
# count and recorded under `excluded_pms` for debugging.
_VALID_SIGNALS: frozenset[str] = frozenset({"bullish", "bearish", "neutral"})


def _pm_signals_for_ticker(analyst_signals: dict, ticker: str) -> list[dict]:
    """Extract per-PM signal dicts for a ticker, augmented with the agent key."""
    out: list[dict] = []
    for agent_key, ticker_map in (analyst_signals or {}).items():
        if agent_key not in _PM_AGENT_KEYS:
            continue
        sig = (ticker_map or {}).get(ticker)
        if not sig:
            continue
        out.append({
            "agent": agent_key,
            "signal": sig.get("signal"),
            "confidence": sig.get("confidence"),
            "reasoning": sig.get("reasoning"),
        })
    return out


def _is_valid_vote(p: dict) -> bool:
    """A vote is countable iff its signal is one of {bullish, bearish, neutral}
    AND its confidence is numeric (int or float, including bool=False/True via
    isinstance — we accept that edge case rather than blacklist it)."""
    return (
        isinstance(p, dict)
        and p.get("signal") in _VALID_SIGNALS
        and isinstance(p.get("confidence"), (int, float))
    )


def _classify(per_pm: list[dict]) -> dict:
    """Classify a list of per-PM signal dicts into a consensus summary.

    Robust to malformed inputs: entries with a signal outside _VALID_SIGNALS
    or with a non-numeric confidence are excluded from voting (and their
    agent ids recorded under "excluded_pms") rather than silently corrupting
    the bull/bear/neu counts.

    Returns dict with:
      type: 'strong' | 'soft' | 'split' | 'diverged' | 'insufficient'
      direction: dominant signal label or 'mixed'
      pm_count: number of VALID votes used for classification
      bullish, bearish, neutral
      confidence_floor: min PM confidence among valid votes (None if none)
      low_conf_pms: agent keys among valid votes with confidence < CONFIDENCE_FLOOR
      excluded_pms: agent keys whose payload failed validation
      members: original per-pm list (for downstream reporting)
    """
    valid_votes = [p for p in per_pm if _is_valid_vote(p)]
    excluded_pms = [
        (p.get("agent") if isinstance(p, dict) else None) or "<unknown>"
        for p in per_pm
        if not _is_valid_vote(p)
    ]

    if len(valid_votes) < 2:
        return {
            "type": "insufficient",
            "direction": "unknown",
            "pm_count": len(valid_votes),
            "bullish": 0, "bearish": 0, "neutral": 0,
            "confidence_floor": None,
            "low_conf_pms": [],
            "excluded_pms": excluded_pms,
            "members": per_pm,
        }

    bull = sum(1 for p in valid_votes if p["signal"] == "bullish")
    bear = sum(1 for p in valid_votes if p["signal"] == "bearish")
    neu = sum(1 for p in valid_votes if p["signal"] == "neutral")

    confs = [p["confidence"] for p in valid_votes]
    conf_floor = min(confs)
    low_conf = [
        p["agent"] for p in valid_votes
        if p["confidence"] < CONFIDENCE_FLOOR
    ]

    distinct = sum(1 for c in (bull, bear, neu) if c > 0)
    total = len(valid_votes)

    if distinct == 1:
        # Unanimous direction
        direction = "bullish" if bull == total else "bearish" if bear == total else "neutral"
        ctype = "strong" if not low_conf else "soft"
    elif distinct == 2:
        ctype = "split"
        # Direction: which non-zero is dominant
        if bull > max(bear, neu):
            direction = "bullish"
        elif bear > max(bull, neu):
            direction = "bearish"
        elif neu > max(bull, bear):
            direction = "neutral"
        else:
            direction = "mixed"
    else:
        # All three signals present
        ctype = "diverged"
        direction = "mixed"

    return {
        "type": ctype,
        "direction": direction,
        "pm_count": total,
        "bullish": bull,
        "bearish": bear,
        "neutral": neu,
        "confidence_floor": conf_floor,
        "low_conf_pms": low_conf,
        "excluded_pms": excluded_pms,
        "members": per_pm,
    }


def cio_consensus_agent(state: AgentState, agent_id: str = "cio_consensus_agent") -> AgentState:
    """Round 1.5 node: classify the PM consensus per ticker.

    Reads state.data.analyst_signals (populated by parallel Round-1 agents)
    and writes state.data.consensus = {ticker: {...}}. Does not modify any
    upstream signals. Idempotent.
    """
    data = state["data"]
    tickers: Iterable[str] = data.get("tickers") or []
    analyst_signals = data.get("analyst_signals") or {}

    consensus: dict[str, dict] = {}
    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Classifying PM consensus")
        per_pm = _pm_signals_for_ticker(analyst_signals, ticker)
        consensus[ticker] = _classify(per_pm)
        progress.update_status(agent_id, ticker, "Done")

    # Show structured reasoning only when --show-reasoning is on, matching
    # how the other agents behave.
    if state.get("metadata", {}).get("show_reasoning"):
        show_agent_reasoning(consensus, "CIO Consensus (Round 1.5)")

    # Merge into existing data (state uses operator-based dict merge — see
    # src/graph/state.merge_dicts). Returning the dict under the same key
    # overwrites, so we wrap to namespace under "consensus".
    return {
        "data": {"consensus": consensus},
        "messages": state.get("messages", []),
    }
