"""Round 2a — each PM generates targeted questions for other PMs.

Per `E:\\Company\\docs\\round-protocol.md` §"Round 2a":

  - 自分の Round 1 出力 + 他 PM の Round 1 出力 を読む
  - 自分の見解と矛盾 or 不足している点を **specific な質問形式** で投げる
  - 質問は必ず宛先 (相手 PM 名) を明示
  - 1 PM あたり最大 3 件
  - 自分の流儀から逸脱した質問はしない (例: Buffett がモメンタムベースの質問はしない)

Output stored at:  state.data.round2a[ticker][asker_agent_key] = [Round2aQuestion]
where each question carries an explicit `target` agent key.

Conditional execution: skipped entirely when consensus.type is "strong" with
DA verdict "proceed", or when consensus is "insufficient".
"""

from __future__ import annotations

import logging

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.agents.round2_common import (
    format_round1_reasoning,
    get_round1_pms_for_ticker,
    methodology_block,
    persona_description,
    should_run_round2,
)
from src.graph.state import AgentState, show_agent_reasoning
from src.utils.llm import call_llm
from src.utils.progress import progress

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema (per PM)
# ---------------------------------------------------------------------------

class Round2aQuestion(BaseModel):
    target: str = Field(description="Agent key of the addressee, e.g. 'charlie_munger_agent'")
    question: str = Field(description="The specific question, written in the asker's voice (<= 250 chars)")
    rationale: str = Field(description="Why this question matters (<= 150 chars)")


class Round2aOutput(BaseModel):
    questions: list[Round2aQuestion] = Field(
        description="Up to 3 questions total across all addressees. Empty list = 'no questions, agree with all others'."
    )
    self_note: str = Field(
        default="",
        description="Optional one-line note from the asker (e.g., 'I have no questions because my domain is orthogonal').",
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TMPL = """You are the {asker_display} agent participating in
Round 2a of a multi-PM investment debate (Q&A format). You have already
produced your Round 1 verdict. Now you must read the other PMs' Round 1
reasoning and emit SPECIFIC questions for them — designed to test their
weakest claims or expose information they may have ignored.

Rules:
  1. Use your own investment philosophy / persona. Do NOT ask questions that
     contradict your own methodology. For example, if you are Warren Buffett
     you do NOT ask momentum-based questions; if you are Druckenmiller you
     do NOT ask about 10-year hold thesis.
  2. Every question must specify an addressee (the `target` field uses the
     exact agent key from the input list).
  3. You may emit up to 3 questions TOTAL across all addressees. Fewer is
     fine — quality beats quantity.
  4. If you have no questions (e.g., your domain is orthogonal to the other
     PMs), emit an empty list and explain in `self_note`.
  5. Questions must be answerable from the data the addressee has
     presumably analyzed. Do not ask for data the addressee couldn't have.

Your methodology (apply its principles when phrasing questions):

---
{methodology}
---

Your persona summary: {persona}

Output a single JSON object matching the schema. No prose."""


_HUMAN_PROMPT_TMPL = """Ticker: {ticker}
Your own Round 1 verdict:
- Signal: {self_signal} (confidence={self_confidence})
- Reasoning: {self_reasoning}

Other PMs' Round 1 verdicts:

{others_block}

Allowed target agent keys (use exactly one of these as `target` in each question):
{allowed_targets}

Now produce your Round 2a questions in the structured-output JSON format."""


# ---------------------------------------------------------------------------
# Per-PM LLM call
# ---------------------------------------------------------------------------

def _default_round2a_output() -> Round2aOutput:
    """Safe fallback when the LLM call fails — no questions, note the error."""
    return Round2aOutput(
        questions=[],
        self_note="Round 2a evaluation unavailable (LLM error). Treat as 'no questions'.",
    )


def _run_round2a_for_asker(
    state: AgentState,
    ticker: str,
    asker_agent_key: str,
    analyst_signals: dict,
    agent_id: str,
) -> dict:
    """Generate one PM's Round 2a questions via a single LLM call."""
    asker_sig = analyst_signals[asker_agent_key].get(ticker, {})
    self_signal = asker_sig.get("signal", "?")
    self_confidence = asker_sig.get("confidence", "?")
    self_reasoning = (asker_sig.get("reasoning") or "").strip()

    others_block = format_round1_reasoning(analyst_signals, ticker, exclude_agent=asker_agent_key)
    allowed_targets = [k for k in get_round1_pms_for_ticker(analyst_signals, ticker) if k != asker_agent_key]
    if not allowed_targets:
        return _default_round2a_output().model_dump() | {"self_note": "Sole PM voter — no one to question."}

    persona = persona_description(asker_agent_key)
    methodology = methodology_block(asker_agent_key) or "(methodology unavailable)"

    template = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT_TMPL),
        ("human", _HUMAN_PROMPT_TMPL),
    ])
    prompt = template.invoke({
        "asker_display": asker_agent_key,
        "methodology": methodology,
        "persona": persona,
        "ticker": ticker,
        "self_signal": self_signal,
        "self_confidence": self_confidence,
        "self_reasoning": self_reasoning,
        "others_block": others_block,
        "allowed_targets": "\n".join(f"  - {k}" for k in allowed_targets),
    })

    signal = call_llm(
        prompt=prompt,
        pydantic_model=Round2aOutput,
        agent_name=agent_id,
        state=state,
        default_factory=_default_round2a_output,
    )

    # Validate that every target is in the allowed set; drop any that aren't
    if isinstance(signal, Round2aOutput):
        allowed_set = set(allowed_targets)
        cleaned = [q for q in signal.questions if q.target in allowed_set]
        if len(cleaned) != len(signal.questions):
            logger.info(
                "Round 2a: dropped %d question(s) from %s that targeted unknown agents",
                len(signal.questions) - len(cleaned),
                asker_agent_key,
            )
        signal.questions = cleaned[:3]  # hard cap of 3
        return signal.model_dump()

    if isinstance(signal, BaseModel):
        return signal.model_dump()
    return _default_round2a_output().model_dump()


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def round_2a_questions_agent(
    state: AgentState,
    agent_id: str = "round_2a_questions_agent",
) -> AgentState:
    """Round 2a node: generate per-PM questions for every ticker that needs Round 2.

    Writes state.data.round2a = {ticker: {asker_agent_key: Round2aOutput-dict}}.
    Tickers whose consensus is strong+proceed or insufficient are skipped.
    """
    data = state["data"]
    tickers: list[str] = list(data.get("tickers") or [])
    consensus_map: dict = data.get("consensus") or {}
    analyst_signals: dict = data.get("analyst_signals") or {}

    round2a: dict[str, dict] = {}

    for ticker in tickers:
        ticker_consensus = consensus_map.get(ticker)
        if not should_run_round2(ticker_consensus):
            progress.update_status(agent_id, ticker, "Skipped (no Round 2 needed)")
            continue

        pms = get_round1_pms_for_ticker(analyst_signals, ticker)
        if len(pms) < 2:
            progress.update_status(agent_id, ticker, "Skipped (need >= 2 PMs)")
            continue

        ticker_2a: dict = {}
        for asker in pms:
            progress.update_status(agent_id, ticker, f"Asking as {asker}")
            try:
                ticker_2a[asker] = _run_round2a_for_asker(
                    state, ticker, asker, analyst_signals, agent_id
                )
            except Exception as e:
                logger.exception("Round 2a failed for %s asking on %s: %s", asker, ticker, e)
                ticker_2a[asker] = _default_round2a_output().model_dump()
        round2a[ticker] = ticker_2a
        progress.update_status(agent_id, ticker, "Done")

    if state.get("metadata", {}).get("show_reasoning") and round2a:
        show_agent_reasoning(round2a, "Round 2a — Questions")

    return {
        "data": {"round2a": round2a},
        "messages": state.get("messages", []),
    }
