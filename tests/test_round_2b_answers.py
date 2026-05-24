"""Unit tests for src/agents/round_2b_answers.py — answer generation."""
from unittest.mock import patch

import pytest

from src.agents.round_2b_answers import (
    Round2bAnswer,
    Round2bOutput,
    _collect_questions_for,
    _default_round2b_output,
    round_2b_answers_agent,
)


@pytest.fixture
def state_with_r2a():
    """State after Round 2a populated questions; ready for Round 2b."""
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
                }
            },
            "round2a": {
                "4751": {
                    "warren_buffett_agent": {
                        "questions": [
                            {"target": "charlie_munger_agent",
                             "question": "Which moat metric is insufficient?",
                             "rationale": "Munger flagged moat data"},
                        ],
                        "self_note": "",
                    },
                    "charlie_munger_agent": {
                        "questions": [
                            {"target": "warren_buffett_agent",
                             "question": "Have you stress-tested DCF against 30% revenue drop?",
                             "rationale": "Stress sensitivity"},
                        ],
                        "self_note": "",
                    },
                }
            },
        },
        "metadata": {},
        "messages": [],
    }


# ----- _collect_questions_for -----

class TestCollectQuestionsFor:
    def test_basic(self):
        r2a = {
            "4751": {
                "warren_buffett_agent": {
                    "questions": [
                        {"target": "charlie_munger_agent", "question": "q1", "rationale": ""}
                    ]
                }
            }
        }
        out = _collect_questions_for("charlie_munger_agent", "4751", r2a)
        assert len(out) == 1
        assert out[0]["asker"] == "warren_buffett_agent"
        assert out[0]["question"] == "q1"

    def test_skips_self_asked(self):
        """A PM never receives their own questions."""
        r2a = {
            "4751": {
                "charlie_munger_agent": {
                    "questions": [
                        {"target": "charlie_munger_agent", "question": "self?", "rationale": ""}
                    ]
                }
            }
        }
        out = _collect_questions_for("charlie_munger_agent", "4751", r2a)
        assert out == []

    def test_filters_by_target(self):
        r2a = {
            "4751": {
                "warren_buffett_agent": {
                    "questions": [
                        {"target": "charlie_munger_agent", "question": "q1", "rationale": ""},
                        {"target": "stanley_druckenmiller_agent", "question": "q2", "rationale": ""},
                    ]
                }
            }
        }
        out = _collect_questions_for("charlie_munger_agent", "4751", r2a)
        assert len(out) == 1
        assert out[0]["question"] == "q1"

    def test_empty_when_no_r2a(self):
        assert _collect_questions_for("a", "4751", {}) == []

    def test_empty_when_no_questions_for_ticker(self):
        assert _collect_questions_for("a", "4751", {"4751": {}}) == []


# ----- Node-level -----

class TestRound2bGating:
    @patch("src.agents.round_2b_answers.call_llm")
    def test_split_consensus_runs(self, mock_call, state_with_r2a):
        mock_call.return_value = Round2bOutput(answers=[])
        round_2b_answers_agent(state_with_r2a)
        # Both Buffett and Munger received 1 question each => 2 LLM calls
        assert mock_call.call_count == 2

    @patch("src.agents.round_2b_answers.call_llm")
    def test_strong_proceed_skips_llm(self, mock_call):
        state = {
            "data": {
                "tickers": ["4751"],
                "analyst_signals": {
                    "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "x"}},
                },
                "consensus": {
                    "4751": {
                        "type": "strong",
                        "devils_advocate": {"recommended_next_step": "proceed"},
                    }
                },
                "round2a": {"4751": {}},
            },
            "metadata": {},
            "messages": [],
        }
        round_2b_answers_agent(state)
        mock_call.assert_not_called()

    @patch("src.agents.round_2b_answers.call_llm")
    def test_pms_with_no_addressed_questions_skipped(self, mock_call):
        """A PM that received zero questions doesn't trigger an LLM call."""
        state = {
            "data": {
                "tickers": ["4751"],
                "analyst_signals": {
                    "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 75, "reasoning": "x"}},
                    "charlie_munger_agent": {"4751": {"signal": "bearish", "confidence": 40, "reasoning": "y"}},
                },
                "consensus": {"4751": {"type": "split"}},
                "round2a": {
                    "4751": {
                        # Only Munger -> Buffett. Buffett -> nobody.
                        "warren_buffett_agent": {"questions": [], "self_note": "no questions"},
                        "charlie_munger_agent": {"questions": [
                            {"target": "warren_buffett_agent", "question": "q", "rationale": "r"}
                        ]},
                    }
                },
            },
            "metadata": {},
            "messages": [],
        }
        mock_call.return_value = Round2bOutput(answers=[])
        round_2b_answers_agent(state)
        # Only Buffett receives a question => 1 LLM call
        assert mock_call.call_count == 1


class TestRound2bOutput:
    @patch("src.agents.round_2b_answers.call_llm")
    def test_writes_answers_to_state(self, mock_call, state_with_r2a):
        mock_call.return_value = Round2bOutput(
            answers=[
                Round2bAnswer(
                    asker="warren_buffett_agent",
                    question="Which moat metric is insufficient?",
                    answer="ROIC consistency over 10 years is missing in EDINET data.",
                    view_changed="partial",
                    change_note="will lower confidence by 10 until ROIC verified",
                ),
            ]
        )
        result = round_2b_answers_agent(state_with_r2a)
        r2b = result["data"]["round2b"]["4751"]
        assert "warren_buffett_agent" in r2b
        assert "charlie_munger_agent" in r2b
        # At least one answer with partial label
        any_partial = any(
            a.get("view_changed") == "partial"
            for payload in r2b.values()
            for a in payload.get("answers", [])
        )
        assert any_partial

    @patch("src.agents.round_2b_answers.call_llm")
    def test_drops_answers_with_unknown_asker(self, mock_call, state_with_r2a):
        """Defensive: if LLM names an asker that didn't address us, drop it."""
        mock_call.return_value = Round2bOutput(
            answers=[
                Round2bAnswer(
                    asker="warren_buffett_agent",
                    question="Which moat metric is insufficient?",
                    answer="real",
                    view_changed="no",
                ),
                Round2bAnswer(
                    asker="cathie_wood_agent",  # never asked
                    question="fabricated",
                    answer="hallucinated",
                    view_changed="full",
                ),
            ]
        )
        result = round_2b_answers_agent(state_with_r2a)
        # The hallucinated asker should not survive validation
        for payload in result["data"]["round2b"]["4751"].values():
            for a in payload.get("answers", []):
                assert a["asker"] != "cathie_wood_agent"

    @patch("src.agents.round_2b_answers.call_llm")
    def test_llm_failure_yields_empty(self, mock_call, state_with_r2a):
        mock_call.side_effect = RuntimeError("timeout")
        result = round_2b_answers_agent(state_with_r2a)
        for payload in result["data"]["round2b"]["4751"].values():
            assert payload["answers"] == []


class TestDefaultOutput:
    def test_safe_default(self):
        out = _default_round2b_output()
        assert out.answers == []
