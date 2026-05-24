"""Unit tests for src/agents/devils_advocate.py — DA sub-round for strong consensus."""
from unittest.mock import patch

import pytest

from src.agents.devils_advocate import (
    CounterArgument,
    DevilsAdvocateSignal,
    PmAssessment,
    _default_da_signal,
    _format_pm_block,
    _should_run_da,
    devils_advocate_agent,
)


# ----- _should_run_da -----

class TestShouldRunDa:
    def test_strong_yes(self):
        assert _should_run_da({"type": "strong"}) is True

    def test_soft_no(self):
        assert _should_run_da({"type": "soft"}) is False

    def test_split_no(self):
        assert _should_run_da({"type": "split"}) is False

    def test_diverged_no(self):
        assert _should_run_da({"type": "diverged"}) is False

    def test_insufficient_no(self):
        assert _should_run_da({"type": "insufficient"}) is False

    def test_none_no(self):
        assert _should_run_da(None) is False

    def test_empty_no(self):
        assert _should_run_da({}) is False


# ----- _format_pm_block -----

class TestFormatPmBlock:
    def test_basic(self):
        members = [
            {"agent": "warren_buffett_agent", "signal": "bullish", "confidence": 80, "reasoning": "ROE strong"},
            {"agent": "charlie_munger_agent", "signal": "bullish", "confidence": 70, "reasoning": "moat solid"},
        ]
        block = _format_pm_block(members)
        assert "warren_buffett_agent" in block
        assert "bullish" in block
        assert "80" in block
        assert "ROE strong" in block

    def test_empty(self):
        assert _format_pm_block([]) == "(no PM data)"

    def test_truncates_long_reasoning(self):
        members = [
            {"agent": "x", "signal": "bullish", "confidence": 80, "reasoning": "a" * 1000}
        ]
        block = _format_pm_block(members)
        assert "..." in block
        # Truncated to ~300 chars
        assert len(block) < 500


# ----- _default_da_signal -----

class TestDefaultDaSignal:
    def test_safe_fallback_shape(self):
        sig = _default_da_signal()
        assert isinstance(sig, DevilsAdvocateSignal)
        # Must produce 3 counter_arguments to satisfy the contract
        assert len(sig.counter_arguments) == 3
        # Conservative defaults: don't pretend consensus survived
        assert sig.consensus_survives is False
        assert sig.recommended_next_step == "trigger_round2"


# ----- devils_advocate_agent (node-level) -----

@pytest.fixture
def strong_consensus_state():
    return {
        "data": {
            "tickers": ["4751"],
            "analyst_signals": {
                "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "Strong ROE"}},
                "charlie_munger_agent": {"4751": {"signal": "bullish", "confidence": 75, "reasoning": "Wide moat"}},
                "stanley_druckenmiller_agent": {"4751": {"signal": "bullish", "confidence": 65, "reasoning": "Momentum positive"}},
            },
            "consensus": {
                "4751": {
                    "type": "strong",
                    "direction": "bullish",
                    "pm_count": 3,
                    "bullish": 3, "bearish": 0, "neutral": 0,
                    "confidence_floor": 65,
                    "low_conf_pms": [],
                    "members": [
                        {"agent": "warren_buffett_agent", "signal": "bullish", "confidence": 80, "reasoning": "Strong ROE"},
                        {"agent": "charlie_munger_agent", "signal": "bullish", "confidence": 75, "reasoning": "Wide moat"},
                        {"agent": "stanley_druckenmiller_agent", "signal": "bullish", "confidence": 65, "reasoning": "Momentum"},
                    ],
                }
            },
        },
        "metadata": {},
        "messages": [],
    }


@pytest.fixture
def split_consensus_state():
    """Split consensus must NOT trigger DA."""
    return {
        "data": {
            "tickers": ["4751"],
            "consensus": {
                "4751": {
                    "type": "split",
                    "direction": "neutral",
                    "pm_count": 3, "bullish": 0, "bearish": 1, "neutral": 2,
                    "confidence_floor": 30, "low_conf_pms": ["charlie_munger_agent"],
                    "members": [],
                }
            },
        },
        "metadata": {},
        "messages": [],
    }


class TestDevilsAdvocateNode:
    @patch("src.agents.devils_advocate.call_llm")
    def test_runs_only_on_strong_consensus(self, mock_call, split_consensus_state):
        """Non-strong consensus must not trigger an LLM call."""
        result = devils_advocate_agent(split_consensus_state)
        mock_call.assert_not_called()
        # Consensus map is returned unchanged (no devils_advocate key added)
        assert "devils_advocate" not in result["data"]["consensus"]["4751"]

    @patch("src.agents.devils_advocate.call_llm")
    def test_runs_on_strong_consensus(self, mock_call, strong_consensus_state):
        """Strong consensus triggers exactly one DA LLM call."""
        mock_call.return_value = DevilsAdvocateSignal(
            counter_arguments=[
                CounterArgument(angle="moat fragility", challenge="c1", if_true_then="t1"),
                CounterArgument(angle="macro pivot", challenge="c2", if_true_then="t2"),
                CounterArgument(angle="valuation", challenge="c3", if_true_then="t3"),
            ],
            pm_assessments=[
                PmAssessment(agent="warren_buffett_agent", is_likely_to_hold=True,
                             confidence_change_estimate=-5, rationale="addressed"),
                PmAssessment(agent="charlie_munger_agent", is_likely_to_hold=True,
                             confidence_change_estimate=-10, rationale="addressed"),
                PmAssessment(agent="stanley_druckenmiller_agent", is_likely_to_hold=True,
                             confidence_change_estimate=-15, rationale="addressed"),
            ],
            consensus_survives=True,
            survival_rationale="All PMs hold; deltas modest.",
            recommended_next_step="proceed",
        )

        result = devils_advocate_agent(strong_consensus_state)
        mock_call.assert_called_once()

        da = result["data"]["consensus"]["4751"]["devils_advocate"]
        assert da["consensus_survives"] is True
        assert da["recommended_next_step"] == "proceed"
        assert len(da["counter_arguments"]) == 3
        assert len(da["pm_assessments"]) == 3

    @patch("src.agents.devils_advocate.call_llm")
    def test_llm_failure_yields_safe_default(self, mock_call, strong_consensus_state):
        """If LLM raises, we record a safe default (consensus_survives=False)."""
        mock_call.side_effect = RuntimeError("LLM timeout")

        result = devils_advocate_agent(strong_consensus_state)
        da = result["data"]["consensus"]["4751"]["devils_advocate"]
        assert da["consensus_survives"] is False
        assert da["recommended_next_step"] == "trigger_round2"
        assert len(da["counter_arguments"]) == 3

    @patch("src.agents.devils_advocate.call_llm")
    def test_no_consensus_means_pass_through(self, mock_call):
        """If Round 1.5 hasn't run (no consensus in state) we no-op."""
        empty_state = {
            "data": {"tickers": ["4751"], "consensus": {}},
            "metadata": {},
            "messages": [],
        }
        result = devils_advocate_agent(empty_state)
        mock_call.assert_not_called()
        assert result["data"] == {}

    @patch("src.agents.devils_advocate.call_llm")
    def test_does_not_mutate_input_state(self, mock_call, strong_consensus_state):
        """Idempotency / safety: input state's consensus dict must not be mutated."""
        mock_call.return_value = _default_da_signal()
        original_consensus = strong_consensus_state["data"]["consensus"]
        original_keys = set(original_consensus["4751"].keys())

        devils_advocate_agent(strong_consensus_state)

        # Input untouched
        assert set(original_consensus["4751"].keys()) == original_keys
        assert "devils_advocate" not in original_consensus["4751"]

    @patch("src.agents.devils_advocate.call_llm")
    def test_per_ticker_isolation(self, mock_call):
        """Two tickers — one strong, one split — only strong gets a DA result."""
        mock_call.return_value = _default_da_signal()
        state = {
            "data": {
                "tickers": ["4751", "8001"],
                "consensus": {
                    "4751": {
                        "type": "strong", "direction": "bullish", "pm_count": 3,
                        "bullish": 3, "bearish": 0, "neutral": 0,
                        "confidence_floor": 70, "low_conf_pms": [],
                        "members": [{"agent": "a", "signal": "bullish", "confidence": 70, "reasoning": "r"}],
                    },
                    "8001": {
                        "type": "split", "direction": "neutral", "pm_count": 3,
                        "bullish": 0, "bearish": 1, "neutral": 2,
                        "confidence_floor": 50, "low_conf_pms": [],
                        "members": [],
                    },
                },
            },
            "metadata": {},
            "messages": [],
        }
        result = devils_advocate_agent(state)
        assert "devils_advocate" in result["data"]["consensus"]["4751"]
        assert "devils_advocate" not in result["data"]["consensus"]["8001"]
        assert mock_call.call_count == 1
