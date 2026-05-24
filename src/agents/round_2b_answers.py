"""Round 2b — each PM answers ONLY the questions addressed to them.

Per `E:\\Company\\docs\\round-protocol.md` §"Round 2b":

  - 自分の Round 1 出力 + 自分宛の質問群を読む
  - 自分宛の質問にだけ回答 (他人宛の質問には触れない)
  - 回答は 2 種類のラベル必須:
    - **見解変化あり (部分的 / 全面)**: 質問を受けて Round 1 見解を修正
    - **見解変化なし**: 修正しない、その理由を明示
  - 答えに窮する質問は「答えられない (情報不足 / 領分外)」と正直に書く

Output stored at:  state.data.round2b[ticker][answerer_agent_key] = [Round2bAnswer]
"""

from __future__ import annotations

import logging

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing_extensions import Literal

from src.agents.round2_common import (
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

class Round2bAnswer(BaseModel):
    asker: str = Field(description="Agent key of the asker, e.g. 'warren_buffett_agent'")
    question: str = Field(description="The original question text (verbatim from Round 2a)")
    answer: str = Field(description="The PM's answer in their own voice (<= 400 chars)")
    view_changed: Literal["full", "partial", "no", "cannot_answer"] = Field(
        description=(
            "'full' = Round 1 verdict reversed; 'partial' = nuance / confidence adjusted; "
            "'no' = answer doesn't change Round 1 stance; "
            "'cannot_answer' = honest declaration of insufficient information."
        )
    )
    change_note: str = Field(
        default="",
        description="If view_changed in {'full','partial'}, briefly note what changed (<= 150 chars).",
    )


class Round2bOutput(BaseModel):
    answers: list[Round2bAnswer] = Field(
        description="One entry per question addressed to this PM. Empty list = 'received no questions'."
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TMPL = """You are the {answerer_display} agent participating
in Round 2b of a multi-PM investment debate. In Round 2a other PMs sent
SPECIFIC questions to you. Now you must answer each question.

Rules:
  1. Answer ONLY the questions addressed to you. Do NOT comment on
     questions sent to other PMs.
  2. Each answer must include a `view_changed` label:
     - "full": Round 1 verdict reversed by this question
     - "partial": Round 1 stance nuanced (e.g., confidence adjusted, scope
       narrowed) but not reversed
     - "no": Round 1 stance fully holds; answer explains why the question
       doesn't move you
     - "cannot_answer": honest declaration when you lack data; this is
       valued more than fabrication
  3. Use your own methodology / voice. Cite specific principles where
     helpful (e.g., "4 Filters Step 2 says...").
  4. Be concise. Answer 400 chars max each.

Your methodology (apply its principles when answering):

---
{methodology}
---

Your persona summary: {persona}

Output a single JSON object matching the Round2bOutput schema. No prose."""


_HUMAN_PROMPT_TMPL = """Ticker: {ticker}
Your own Round 1 verdict (this is your starting position):
- Signal: {self_signal} (confidence={self_confidence})
- Reasoning: {self_reasoning}

Questions addressed to YOU in Round 2a:

{addressed_questions}

For each question above, produce a Round2bAnswer entry."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_questions_for(
    answerer: str,
    ticker: str,
    round2a: dict,
) -> list[dict]:
    """Walk Round 2a output and return [{asker, question, rationale}] for this answerer."""
    out: list[dict] = []
    ticker_2a = (round2a or {}).get(ticker) or {}
    for asker_key, asker_payload in ticker_2a.items():
        if asker_key == answerer:
            continue
        for q in (asker_payload.get("questions") or []):
            if q.get("target") == answerer:
                out.append({
                    "asker": asker_key,
                    "question": q.get("question", ""),
                    "rationale": q.get("rationale", ""),
                })
    return out


def _default_round2b_output() -> Round2bOutput:
    return Round2bOutput(answers=[])


def _format_addressed_block(questions: list[dict]) -> str:
    if not questions:
        return "(no questions addressed to you)"
    lines: list[str] = []
    for i, q in enumerate(questions, 1):
        rationale_suffix = f"\n   Rationale: {q['rationale']}" if q.get("rationale") else ""
        lines.append(f"{i}. From: {q['asker']}\n   Question: {q['question']}{rationale_suffix}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Per-PM LLM call
# ---------------------------------------------------------------------------

def _run_round2b_for_answerer(
    state: AgentState,
    ticker: str,
    answerer_agent_key: str,
    analyst_signals: dict,
    round2a: dict,
    agent_id: str,
) -> dict:
    """Generate one PM's Round 2b answers via a single LLM call."""
    addressed = _collect_questions_for(answerer_agent_key, ticker, round2a)
    if not addressed:
        return _default_round2b_output().model_dump()

    self_sig = analyst_signals[answerer_agent_key].get(ticker, {})
    self_signal = self_sig.get("signal", "?")
    self_confidence = self_sig.get("confidence", "?")
    self_reasoning = (self_sig.get("reasoning") or "").strip()

    persona = persona_description(answerer_agent_key)
    methodology = methodology_block(answerer_agent_key) or "(methodology unavailable)"

    template = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT_TMPL),
        ("human", _HUMAN_PROMPT_TMPL),
    ])
    prompt = template.invoke({
        "answerer_display": answerer_agent_key,
        "methodology": methodology,
        "persona": persona,
        "ticker": ticker,
        "self_signal": self_signal,
        "self_confidence": self_confidence,
        "self_reasoning": self_reasoning,
        "addressed_questions": _format_addressed_block(addressed),
    })

    signal = call_llm(
        prompt=prompt,
        pydantic_model=Round2bOutput,
        agent_name=agent_id,
        state=state,
        default_factory=_default_round2b_output,
    )

    if isinstance(signal, Round2bOutput):
        # Defensive: ensure the answers cite real askers from addressed[]
        valid_askers = {q["asker"] for q in addressed}
        cleaned = [a for a in signal.answers if a.asker in valid_askers]
        if len(cleaned) != len(signal.answers):
            logger.info(
                "Round 2b: dropped %d answer(s) from %s that named unknown askers",
                len(signal.answers) - len(cleaned),
                answerer_agent_key,
            )
        signal.answers = cleaned
        return signal.model_dump()

    if isinstance(signal, BaseModel):
        return signal.model_dump()
    return _default_round2b_output().model_dump()


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def round_2b_answers_agent(
    state: AgentState,
    agent_id: str = "round_2b_answers_agent",
) -> AgentState:
    """Round 2b node: each PM answers their addressed questions.

    Writes state.data.round2b = {ticker: {answerer: Round2bOutput-dict}}.
    """
    data = state["data"]
    tickers: list[str] = list(data.get("tickers") or [])
    consensus_map: dict = data.get("consensus") or {}
    analyst_signals: dict = data.get("analyst_signals") or {}
    round2a: dict = data.get("round2a") or {}

    round2b: dict[str, dict] = {}

    for ticker in tickers:
        ticker_consensus = consensus_map.get(ticker)
        if not should_run_round2(ticker_consensus):
            progress.update_status(agent_id, ticker, "Skipped (no Round 2 needed)")
            continue
        if ticker not in round2a:
            progress.update_status(agent_id, ticker, "Skipped (no Round 2a questions to answer)")
            continue

        ticker_2b: dict = {}
        for answerer in get_round1_pms_for_ticker(analyst_signals, ticker):
            progress.update_status(agent_id, ticker, f"Answering as {answerer}")
            try:
                ticker_2b[answerer] = _run_round2b_for_answerer(
                    state, ticker, answerer, analyst_signals, round2a, agent_id
                )
            except Exception as e:
                logger.exception("Round 2b failed for %s on %s: %s", answerer, ticker, e)
                ticker_2b[answerer] = _default_round2b_output().model_dump()
        round2b[ticker] = ticker_2b
        progress.update_status(agent_id, ticker, "Done")

    if state.get("metadata", {}).get("show_reasoning") and round2b:
        show_agent_reasoning(round2b, "Round 2b — Answers")

    return {
        "data": {"round2b": round2b},
        "messages": state.get("messages", []),
    }
