"""Devil's Advocate sub-round for Strong Consensus tickers.

Per `E:\\Company\\docs\\round-protocol.md` §"Devil's Advocate サブラウンド":

> Strong Consensus に達した時、必ず以下を実施する (groupthink 排除):
> 1. CIO が意図的に逆の立場を取り、3 PM の判断に反論を投げる
> 2. PM が短文で再反論
> 3. 反論を受けても結論が揺らがなければ Strong Consensus 確定
> 4. 揺らげば Round 2 を実施

This module implements an MVP of step 1 + a CIO-side simulation of steps 2-3
(single LLM call). A future session can replace the CIO-side simulation with
per-PM re-queries for higher fidelity.

The node is a pass-through for any ticker whose consensus is NOT 'strong'.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.graph.state import AgentState, show_agent_reasoning
from src.utils.llm import call_llm
from src.utils.progress import progress

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class CounterArgument(BaseModel):
    angle: str = Field(description="One-line label for the counter-attack angle, e.g. 'moat fragility'")
    challenge: str = Field(description="The specific contrary claim, written in a CIO devil's-advocate voice (under 200 chars)")
    if_true_then: str = Field(description="What the implication would be if this counter is correct (under 150 chars)")


class PmAssessment(BaseModel):
    agent: str = Field(description="The PM agent key, e.g. 'warren_buffett_agent'")
    is_likely_to_hold: bool = Field(description="True if this PM's stated reasoning anticipates / survives the counter-arguments")
    confidence_change_estimate: int = Field(description="Estimated post-DA confidence delta in [-50, +50]")
    rationale: str = Field(description="Why the PM would or wouldn't budge (under 200 chars)")


class DevilsAdvocateSignal(BaseModel):
    counter_arguments: list[CounterArgument] = Field(description="Exactly 3 counter-arguments to the strong consensus")
    pm_assessments: list[PmAssessment] = Field(description="One assessment per PM in the consensus")
    consensus_survives: bool = Field(description="True if the strong consensus likely survives the DA challenge")
    survival_rationale: str = Field(description="One-sentence rationale for the survives verdict (under 200 chars)")
    recommended_next_step: Literal["proceed", "trigger_round2"] = Field(
        description="'proceed' if survives, 'trigger_round2' if any major PM would break"
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_DA_SYSTEM_PROMPT = """You are the Chief Investment Officer running a Devil's
Advocate sub-round on a STRONG-consensus recommendation. All Portfolio
Managers agree on the same direction with reasonable confidence. Your job
is to deliberately challenge that agreement to expose groupthink risk.

Process you must follow:

1. Construct EXACTLY 3 counter-arguments to the consensus direction. They
   must be specific to the ticker / facts shown, NOT generic skepticism.
   Use distinct angles where possible (e.g., business-model risk, macro
   pivot, valuation, governance, technical setup).

2. For each PM in the consensus, estimate whether their stated reasoning
   already anticipates / survives these counter-arguments. Output:
     - is_likely_to_hold: bool
     - confidence_change_estimate: integer in [-50, +50]
     - rationale: short text

3. Conclude:
   - consensus_survives: True only if a majority of PMs would hold AND no
     single PM would lose >= 30 confidence points.
   - recommended_next_step: 'proceed' if survives, else 'trigger_round2'.

Be honest. If you cannot find 3 credible counter-arguments, the consensus
genuinely is robust; say so in survival_rationale but still produce 3 angles.
Never invent facts not present in the per-PM reasoning. Return JSON only."""


_DA_HUMAN_PROMPT = """Ticker: {ticker}
Consensus direction: {direction}
Number of PMs in consensus: {pm_count}
Confidence floor: {confidence_floor}

Per-PM Round 1 verdicts:
{pm_block}

Run the Devil's Advocate process and return the JSON object specified by the
schema (counter_arguments, pm_assessments, consensus_survives,
survival_rationale, recommended_next_step)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _should_run_da(ticker_consensus: dict | None) -> bool:
    """DA runs only on Strong Consensus (per round-protocol.md §Early Consensus)."""
    if not ticker_consensus:
        return False
    return ticker_consensus.get("type") == "strong"


def _format_pm_block(members: list[dict]) -> str:
    """Render per-PM signals into a compact block for the LLM prompt."""
    if not members:
        return "(no PM data)"
    lines = []
    for m in members:
        agent = m.get("agent", "?")
        sig = m.get("signal", "?")
        conf = m.get("confidence", "?")
        reasoning = (m.get("reasoning") or "").replace("\n", " ").strip()
        if len(reasoning) > 300:
            reasoning = reasoning[:297] + "..."
        lines.append(f"- {agent} [{sig}, confidence={conf}]: {reasoning}")
    return "\n".join(lines)


def _default_da_signal() -> DevilsAdvocateSignal:
    """Fallback when the LLM call fails — flag as 'cannot evaluate'."""
    return DevilsAdvocateSignal(
        counter_arguments=[
            CounterArgument(
                angle="evaluation_unavailable",
                challenge="Devil's Advocate sub-round could not be evaluated due to LLM error.",
                if_true_then="Shareholder should manually challenge the consensus before acting.",
            )
        ] * 3,
        pm_assessments=[],
        consensus_survives=False,  # err on side of caution
        survival_rationale="LLM evaluation failed; defaulting to 'cannot confirm survival'.",
        recommended_next_step="trigger_round2",
    )


def _run_da_for_ticker(
    state: AgentState,
    ticker: str,
    ticker_consensus: dict,
    agent_id: str,
) -> dict:
    """Execute one DA LLM call for a single ticker."""
    members = ticker_consensus.get("members", []) or []
    direction = ticker_consensus.get("direction", "?")
    pm_count = ticker_consensus.get("pm_count", len(members))
    confidence_floor = ticker_consensus.get("confidence_floor", "?")
    pm_block = _format_pm_block(members)

    template = ChatPromptTemplate.from_messages([
        ("system", _DA_SYSTEM_PROMPT),
        ("human", _DA_HUMAN_PROMPT),
    ])

    prompt = template.invoke({
        "ticker": ticker,
        "direction": direction,
        "pm_count": pm_count,
        "confidence_floor": confidence_floor,
        "pm_block": pm_block,
    })

    signal = call_llm(
        prompt=prompt,
        pydantic_model=DevilsAdvocateSignal,
        agent_name=agent_id,
        state=state,
        default_factory=_default_da_signal,
    )

    if isinstance(signal, DevilsAdvocateSignal):
        return signal.model_dump()
    if isinstance(signal, BaseModel):
        return signal.model_dump()
    # Last resort: it should already be a dict-shaped object
    return json.loads(signal.json()) if hasattr(signal, "json") else dict(signal)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def devils_advocate_agent(
    state: AgentState,
    agent_id: str = "devils_advocate_agent",
) -> AgentState:
    """Conditional node: runs DA sub-round on every ticker whose consensus is 'strong'.

    Writes the result onto state.data.consensus[ticker]['devils_advocate'].
    Tickers with non-strong consensus are passed through unchanged.

    Idempotent: re-running on the same state replaces the prior DA result.
    """
    data = state["data"]
    tickers: list[str] = list(data.get("tickers") or [])
    consensus_map: dict = dict(data.get("consensus") or {})  # shallow copy so we mutate safely

    if not consensus_map:
        # Round 1.5 hasn't run — nothing to challenge
        return {"data": {}, "messages": state.get("messages", [])}

    for ticker in tickers:
        ticker_consensus = consensus_map.get(ticker)
        if not _should_run_da(ticker_consensus):
            progress.update_status(agent_id, ticker, "Skipped (non-strong)")
            continue

        progress.update_status(agent_id, ticker, "Challenging consensus")
        try:
            da_result = _run_da_for_ticker(state, ticker, ticker_consensus, agent_id)
        except Exception as e:
            logger.exception("Devil's Advocate failed for %s: %s", ticker, e)
            da_result = _default_da_signal().model_dump()

        # Mutate a copy of the ticker consensus to avoid in-place state surprises
        updated = dict(ticker_consensus)
        updated["devils_advocate"] = da_result
        consensus_map[ticker] = updated
        progress.update_status(agent_id, ticker, "Done")

    if state.get("metadata", {}).get("show_reasoning"):
        show_agent_reasoning(
            {t: c.get("devils_advocate") for t, c in consensus_map.items() if c.get("devils_advocate")},
            "Devil's Advocate (Strong Consensus only)",
        )

    return {
        "data": {"consensus": consensus_map},
        "messages": state.get("messages", []),
    }
