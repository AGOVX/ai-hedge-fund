"""Unit tests for src/agents/round_2a_questions.py — question generation."""
from unittest.mock import patch

import pytest

from src.agents.round_2a_questions import (
    Round2aOutput,
    Round2aQuestion,
    _default_round2a_output,
    round_2a_questions_agent,
)


@pytest.fixture
def split_state():
    """Two-PM split consensus, both with Round 1 signals — Round 2 must run."""
    return {
        "data": {
            "tickers": ["4751"],
            "analyst_signals": {
                "warren_buffett_agent": {
                    "4751": {"signal": "bullish", "confidence": 75, "reasoning": "Strong ROE"},
                },
                "charlie_munger_agent": {
                    "4751": {"signal": "bearish", "confidence": 40, "reasoning": "Insufficient moat data"},
                },
            },
            "consensus": {
                "4751": {
                    "type": "split", "direction": "mixed",
                    "pm_count": 2, "bullish": 1, "bearish": 1, "neutral": 0,
                    "confidence_floor": 40, "low_conf_pms": ["charlie_munger_agent"],
                    "members": [],
                }
            },
        },
        "metadata": {},
        "messages": [],
    }


@pytest.fixture
def strong_proceed_state():
    """Strong consensus with DA verdict 'proceed' — Round 2a MUST skip."""
    return {
        "data": {
            "tickers": ["4751"],
            "analyst_signals": {
                "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "x"}},
                "charlie_munger_agent": {"4751": {"signal": "bullish", "confidence": 75, "reasoning": "y"}},
            },
            "consensus": {
                "4751": {
                    "type": "strong", "direction": "bullish",
                    "pm_count": 2, "bullish": 2, "bearish": 0, "neutral": 0,
                    "confidence_floor": 75, "low_conf_pms": [],
                    "devils_advocate": {"recommended_next_step": "proceed"},
                }
            },
        },
        "metadata": {},
        "messages": [],
    }


class TestRound2aGating:
    @patch("src.agents.round_2a_questions.call_llm")
    def test_split_consensus_triggers_llm_calls(self, mock_call, split_state):
        mock_call.return_value = Round2aOutput(questions=[], self_note="ok")
        round_2a_questions_agent(split_state)
        # 2 PMs => 2 LLM calls (one per asker)
        assert mock_call.call_count == 2

    @patch("src.agents.round_2a_questions.call_llm")
    def test_strong_proceed_skips_llm_entirely(self, mock_call, strong_proceed_state):
        result = round_2a_questions_agent(strong_proceed_state)
        mock_call.assert_not_called()
        # Nothing written for skipped tickers
        assert result["data"]["round2a"] == {}

    @patch("src.agents.round_2a_questions.call_llm")
    def test_insufficient_consensus_skips_llm(self, mock_call):
        state = {
            "data": {
                "tickers": ["4751"],
                "analyst_signals": {},
                "consensus": {"4751": {"type": "insufficient"}},
            },
            "metadata": {},
            "messages": [],
        }
        round_2a_questions_agent(state)
        mock_call.assert_not_called()

    @patch("src.agents.round_2a_questions.call_llm")
    def test_single_pm_skipped(self, mock_call):
        """If only 1 PM ran Round 1, there's no one to question — skip."""
        state = {
            "data": {
                "tickers": ["4751"],
                "analyst_signals": {
                    "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 75, "reasoning": "x"}},
                },
                "consensus": {
                    "4751": {"type": "soft", "members": []}
                },
            },
            "metadata": {},
            "messages": [],
        }
        round_2a_questions_agent(state)
        mock_call.assert_not_called()


class TestRound2aOutput:
    @patch("src.agents.round_2a_questions.call_llm")
    def test_writes_questions_to_state(self, mock_call, split_state):
        # Use a fresh Round2aOutput per call so the in-function mutation
        # `signal.questions = cleaned[:3]` from one iteration doesn't leak
        # into the next. The question's `target` is set to the OTHER asker so
        # both Buffett-asking and Munger-asking sides survive validation.
        def fresh_output(*args, **kwargs):
            return Round2aOutput(
                questions=[
                    Round2aQuestion(
                        target="warren_buffett_agent",
                        question="Have you stress-tested the DCF against a 30% revenue drop?",
                        rationale="Asker wants to confirm sensitivity",
                    ),
                    Round2aQuestion(
                        target="charlie_munger_agent",
                        question="Which specific moat metric do you find lacking?",
                        rationale="Munger flagged moat data",
                    ),
                ],
                self_note="",
            )
        mock_call.side_effect = fresh_output
        result = round_2a_questions_agent(split_state)
        r2a = result["data"]["round2a"]["4751"]
        # Both askers' payloads are present
        assert "warren_buffett_agent" in r2a
        assert "charlie_munger_agent" in r2a
        # Both askers should have at least one question that survived
        # validation (their non-self target is in the allowed set).
        for asker, payload in r2a.items():
            assert payload.get("questions"), f"{asker} produced no questions"

    @patch("src.agents.round_2a_questions.call_llm")
    def test_drops_questions_targeting_unknown_agents(self, mock_call, split_state):
        """Defensive: if LLM hallucinates a target agent not in Round 1, drop it."""
        mock_call.return_value = Round2aOutput(
            questions=[
                Round2aQuestion(target="warren_buffett_agent", question="real q", rationale="r"),
                Round2aQuestion(target="cathie_wood_agent", question="hallucinated", rationale="r"),
            ],
        )
        result = round_2a_questions_agent(split_state)
        for asker_payload in result["data"]["round2a"]["4751"].values():
            for q in asker_payload["questions"]:
                # asker won't be in the target list (you can't ask yourself), so just
                # ensure no hallucinated agents survived
                assert q["target"] != "cathie_wood_agent"

    @patch("src.agents.round_2a_questions.call_llm")
    def test_caps_at_3_questions(self, mock_call, split_state):
        mock_call.return_value = Round2aOutput(
            questions=[
                Round2aQuestion(target="charlie_munger_agent", question=f"q{i}", rationale="r")
                for i in range(10)
            ]
        )
        result = round_2a_questions_agent(split_state)
        for asker_payload in result["data"]["round2a"]["4751"].values():
            assert len(asker_payload["questions"]) <= 3

    @patch("src.agents.round_2a_questions.call_llm")
    def test_llm_failure_yields_empty_questions(self, mock_call, split_state):
        mock_call.side_effect = RuntimeError("timeout")
        result = round_2a_questions_agent(split_state)
        r2a = result["data"]["round2a"]["4751"]
        for asker_payload in r2a.values():
            assert asker_payload["questions"] == []


class TestDefaultRound2aOutput:
    def test_safe_default(self):
        out = _default_round2a_output()
        assert out.questions == []
        assert "LLM error" in out.self_note
