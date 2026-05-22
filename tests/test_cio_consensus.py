"""Unit tests for src/agents/cio_consensus.py — Round 1.5 consensus detector."""
from src.agents.cio_consensus import (
    CONFIDENCE_FLOOR,
    _classify,
    _pm_signals_for_ticker,
    cio_consensus_agent,
)


# ----- _pm_signals_for_ticker -----

class TestPmSignalsForTicker:
    def test_filters_to_pm_agents_only(self):
        analyst_signals = {
            "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "x"}},
            "fundamentals_analyst_agent": {"4751": {"signal": "bullish", "confidence": 70, "reasoning": "y"}},
            "sentiment_analyst_agent": {"4751": {"signal": "neutral", "confidence": 50, "reasoning": "z"}},
        }
        out = _pm_signals_for_ticker(analyst_signals, "4751")
        assert len(out) == 1
        assert out[0]["agent"] == "warren_buffett_agent"

    def test_skips_other_tickers(self):
        analyst_signals = {
            "warren_buffett_agent": {
                "4751": {"signal": "bullish", "confidence": 80, "reasoning": "x"},
                "8001": {"signal": "neutral", "confidence": 60, "reasoning": "y"},
            },
        }
        assert len(_pm_signals_for_ticker(analyst_signals, "4751")) == 1
        assert len(_pm_signals_for_ticker(analyst_signals, "8001")) == 1
        assert len(_pm_signals_for_ticker(analyst_signals, "MISSING")) == 0

    def test_skips_agents_without_ticker_signal(self):
        analyst_signals = {
            "warren_buffett_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "x"}},
            "charlie_munger_agent": {},  # ran but produced no signal
        }
        out = _pm_signals_for_ticker(analyst_signals, "4751")
        assert len(out) == 1


# ----- _classify -----

class TestClassifyInsufficient:
    def test_zero_pms(self):
        out = _classify([])
        assert out["type"] == "insufficient"
        assert out["pm_count"] == 0
        assert out["confidence_floor"] is None

    def test_one_pm(self):
        out = _classify([
            {"agent": "warren_buffett_agent", "signal": "bullish", "confidence": 80, "reasoning": "x"},
        ])
        assert out["type"] == "insufficient"
        assert out["pm_count"] == 1


class TestClassifyStrong:
    def test_all_bullish_high_conf(self):
        per_pm = [
            {"agent": "warren_buffett_agent", "signal": "bullish", "confidence": 80, "reasoning": "x"},
            {"agent": "charlie_munger_agent", "signal": "bullish", "confidence": 70, "reasoning": "y"},
            {"agent": "stanley_druckenmiller_agent", "signal": "bullish", "confidence": 65, "reasoning": "z"},
        ]
        out = _classify(per_pm)
        assert out["type"] == "strong"
        assert out["direction"] == "bullish"
        assert out["bullish"] == 3
        assert out["confidence_floor"] == 65
        assert out["low_conf_pms"] == []

    def test_all_bearish_high_conf(self):
        out = _classify([
            {"agent": "a", "signal": "bearish", "confidence": 80, "reasoning": "x"},
            {"agent": "b", "signal": "bearish", "confidence": 60, "reasoning": "y"},
        ])
        assert out["type"] == "strong"
        assert out["direction"] == "bearish"

    def test_all_neutral_high_conf(self):
        out = _classify([
            {"agent": "a", "signal": "neutral", "confidence": 80, "reasoning": "x"},
            {"agent": "b", "signal": "neutral", "confidence": 50, "reasoning": "y"},  # exactly at floor
        ])
        assert out["type"] == "strong"
        assert out["direction"] == "neutral"


class TestClassifySoft:
    def test_all_same_with_low_conf_pm(self):
        per_pm = [
            {"agent": "warren_buffett_agent", "signal": "bullish", "confidence": 80, "reasoning": "x"},
            {"agent": "charlie_munger_agent", "signal": "bullish", "confidence": 70, "reasoning": "y"},
            {"agent": "stanley_druckenmiller_agent", "signal": "bullish", "confidence": 30, "reasoning": "z"},
        ]
        out = _classify(per_pm)
        assert out["type"] == "soft"
        assert out["direction"] == "bullish"
        assert out["confidence_floor"] == 30
        assert out["low_conf_pms"] == ["stanley_druckenmiller_agent"]


class TestClassifySplit:
    def test_two_distinct_signals(self):
        # Mimics the live 4751 run: 2 neutral + 1 bearish (Buffett, Druckenmiller, Munger)
        per_pm = [
            {"agent": "warren_buffett_agent", "signal": "neutral", "confidence": 55, "reasoning": "x"},
            {"agent": "stanley_druckenmiller_agent", "signal": "neutral", "confidence": 30, "reasoning": "y"},
            {"agent": "charlie_munger_agent", "signal": "bearish", "confidence": 30, "reasoning": "z"},
        ]
        out = _classify(per_pm)
        assert out["type"] == "split"
        # Dominant is neutral (2 > 1)
        assert out["direction"] == "neutral"
        assert out["neutral"] == 2
        assert out["bearish"] == 1

    def test_50_50_split_returns_mixed(self):
        # Equal split: 1 bull + 1 bear, 0 neutral
        out = _classify([
            {"agent": "a", "signal": "bullish", "confidence": 60, "reasoning": "x"},
            {"agent": "b", "signal": "bearish", "confidence": 70, "reasoning": "y"},
        ])
        assert out["type"] == "split"
        assert out["direction"] == "mixed"


class TestClassifyDiverged:
    def test_three_distinct_signals(self):
        per_pm = [
            {"agent": "warren_buffett_agent", "signal": "bullish", "confidence": 75, "reasoning": "x"},
            {"agent": "charlie_munger_agent", "signal": "neutral", "confidence": 60, "reasoning": "y"},
            {"agent": "stanley_druckenmiller_agent", "signal": "bearish", "confidence": 65, "reasoning": "z"},
        ]
        out = _classify(per_pm)
        assert out["type"] == "diverged"
        assert out["direction"] == "mixed"


# ----- cio_consensus_agent (node-level integration) -----

class TestCioConsensusAgent:
    def test_writes_per_ticker_consensus_to_state(self):
        state = {
            "data": {
                "tickers": ["4751", "8001"],
                "analyst_signals": {
                    "warren_buffett_agent": {
                        "4751": {"signal": "neutral", "confidence": 55, "reasoning": "a"},
                        "8001": {"signal": "bullish", "confidence": 75, "reasoning": "b"},
                    },
                    "charlie_munger_agent": {
                        "4751": {"signal": "bearish", "confidence": 30, "reasoning": "c"},
                        "8001": {"signal": "bullish", "confidence": 65, "reasoning": "d"},
                    },
                    "stanley_druckenmiller_agent": {
                        "4751": {"signal": "neutral", "confidence": 30, "reasoning": "e"},
                        "8001": {"signal": "bullish", "confidence": 70, "reasoning": "f"},
                    },
                },
            },
            "metadata": {},
            "messages": [],
        }
        result = cio_consensus_agent(state)
        cons = result["data"]["consensus"]
        # 4751: 2 neutral + 1 bearish -> split, dominant neutral
        assert cons["4751"]["type"] == "split"
        assert cons["4751"]["direction"] == "neutral"
        # 8001: all bullish, all conf >= 50 -> strong
        assert cons["8001"]["type"] == "strong"
        assert cons["8001"]["direction"] == "bullish"

    def test_empty_signals_yields_insufficient(self):
        state = {
            "data": {"tickers": ["4751"], "analyst_signals": {}},
            "metadata": {},
            "messages": [],
        }
        result = cio_consensus_agent(state)
        assert result["data"]["consensus"]["4751"]["type"] == "insufficient"

    def test_only_specialist_analysts_yields_insufficient(self):
        """Specialist analysts (sentiment, fundamentals, etc.) don't count as PM votes."""
        state = {
            "data": {
                "tickers": ["4751"],
                "analyst_signals": {
                    "fundamentals_analyst_agent": {"4751": {"signal": "bullish", "confidence": 80, "reasoning": "x"}},
                    "sentiment_analyst_agent": {"4751": {"signal": "bullish", "confidence": 70, "reasoning": "y"}},
                },
            },
            "metadata": {},
            "messages": [],
        }
        result = cio_consensus_agent(state)
        assert result["data"]["consensus"]["4751"]["type"] == "insufficient"


class TestConfidenceFloorConstant:
    def test_floor_is_50(self):
        # If this fails the round-protocol contract changed; review tests.
        assert CONFIDENCE_FLOOR == 50
