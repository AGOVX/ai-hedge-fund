"""Unit tests for src/agents/round2_common.py — shared helpers for Round 2."""
import pytest

from src.agents.round2_common import (
    agent_key_to_methodology_key,
    format_round1_reasoning,
    get_round1_pms_for_ticker,
    methodology_block,
    persona_description,
    should_run_round2,
)


# ----- should_run_round2 (firing condition gate) -----

class TestShouldRunRound2:
    def test_none_no(self):
        assert should_run_round2(None) is False

    def test_empty_no(self):
        assert should_run_round2({}) is False

    def test_insufficient_no(self):
        assert should_run_round2({"type": "insufficient"}) is False

    def test_strong_proceed_no(self):
        consensus = {
            "type": "strong",
            "devils_advocate": {"recommended_next_step": "proceed"},
        }
        assert should_run_round2(consensus) is False

    def test_strong_trigger_round2_yes(self):
        consensus = {
            "type": "strong",
            "devils_advocate": {"recommended_next_step": "trigger_round2"},
        }
        assert should_run_round2(consensus) is True

    def test_strong_no_da_block_no(self):
        """If DA didn't run somehow, default to NOT triggering R2 from strong."""
        assert should_run_round2({"type": "strong"}) is False

    def test_soft_yes(self):
        assert should_run_round2({"type": "soft"}) is True

    def test_split_yes(self):
        assert should_run_round2({"type": "split"}) is True

    def test_diverged_yes(self):
        assert should_run_round2({"type": "diverged"}) is True


# ----- Agent-key utilities -----

class TestAgentKeyUtils:
    def test_methodology_key_strips_agent_suffix(self):
        assert agent_key_to_methodology_key("warren_buffett_agent") == "warren_buffett"
        assert agent_key_to_methodology_key("stanley_druckenmiller_agent") == "stanley_druckenmiller"

    def test_methodology_key_passthrough_when_no_suffix(self):
        assert agent_key_to_methodology_key("foo_bar") == "foo_bar"


class TestPersonaDescription:
    def test_known_pm_returns_display_name_and_style(self):
        desc = persona_description("warren_buffett_agent")
        assert "Warren Buffett" in desc
        assert "Oracle of Omaha" in desc

    def test_unknown_returns_key(self):
        # Falls back to the raw agent key when not in ANALYST_CONFIG
        assert persona_description("totally_invented_agent") == "totally_invented_agent"


class TestMethodologyBlock:
    def test_warren_buffett_returns_nonempty(self):
        # Methodology file ported in F-1; should be >= 1k chars
        text = methodology_block("warren_buffett_agent")
        assert isinstance(text, str)
        assert len(text) >= 1000

    def test_unknown_returns_empty(self):
        # Missing methodology files return "" (graceful fallback)
        text = methodology_block("never_existed_agent")
        assert text == ""


# ----- get_round1_pms_for_ticker -----

class TestGetRound1Pms:
    def test_filters_to_pm_agents_only(self):
        analyst_signals = {
            "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80}},
            "fundamentals_analyst_agent": {"4751": {"signal": "bullish", "confidence": 70}},
        }
        out = get_round1_pms_for_ticker(analyst_signals, "4751")
        assert out == ["warren_buffett_agent"]

    def test_skips_pms_without_signal_for_ticker(self):
        analyst_signals = {
            "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80}},
            "charlie_munger_agent": {"8001": {"signal": "neutral", "confidence": 50}},
        }
        out = get_round1_pms_for_ticker(analyst_signals, "4751")
        assert out == ["warren_buffett_agent"]

    def test_sorted_for_determinism(self):
        analyst_signals = {
            "stanley_druckenmiller_agent": {"X": {"signal": "bullish", "confidence": 60}},
            "charlie_munger_agent": {"X": {"signal": "bullish", "confidence": 70}},
            "warren_buffett_agent": {"X": {"signal": "bullish", "confidence": 80}},
        }
        out = get_round1_pms_for_ticker(analyst_signals, "X")
        # Alphabetical
        assert out == ["charlie_munger_agent", "stanley_druckenmiller_agent", "warren_buffett_agent"]


# ----- format_round1_reasoning -----

class TestFormatRound1Reasoning:
    def test_basic(self):
        analyst_signals = {
            "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "Strong moat"}},
            "charlie_munger_agent": {"4751": {"signal": "neutral", "confidence": 50, "reasoning": "Wait"}},
        }
        text = format_round1_reasoning(analyst_signals, "4751")
        assert "warren_buffett_agent" in text
        assert "Strong moat" in text
        assert "charlie_munger_agent" in text

    def test_exclude_agent(self):
        analyst_signals = {
            "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "X"}},
            "charlie_munger_agent": {"4751": {"signal": "neutral", "confidence": 50, "reasoning": "Y"}},
        }
        text = format_round1_reasoning(analyst_signals, "4751", exclude_agent="warren_buffett_agent")
        assert "warren_buffett_agent" not in text
        assert "charlie_munger_agent" in text

    def test_empty_returns_placeholder(self):
        text = format_round1_reasoning({}, "4751")
        assert "no other Round 1 signals" in text

    def test_truncates_long_reasoning(self):
        analyst_signals = {
            "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "a" * 2000}},
        }
        text = format_round1_reasoning(analyst_signals, "4751", max_len=300)
        assert "..." in text
        # Block should be much shorter than 2000 chars
        assert len(text) < 800
