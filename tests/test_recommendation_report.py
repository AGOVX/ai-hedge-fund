"""Unit tests for REC report formatter + watchlist updater (F-7)."""

from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from src.utils.recommendation_report import (
    _consensus_label,
    _gov_warning_for_action,
    _human_agent_name,
    emit_reports_for_run,
    format_report,
    next_rec_id,
    save_report,
)
from src.utils.watchlist_update import (
    _decide_status,
    _signal_summary,
    append_or_update,
    update_watchlist_for_run,
)


# ---------------- Pure helpers ----------------

class TestConsensusLabelWithCioInput:
    """When CIO Round 1.5 consensus dict is provided, prefer its classification."""

    def test_strong_consensus_format(self):
        cio = {
            "type": "strong", "direction": "bullish",
            "pm_count": 3, "bullish": 3, "bearish": 0, "neutral": 0,
            "confidence_floor": 70, "low_conf_pms": [],
        }
        label = _consensus_label([{"signal": "bullish"}] * 3, cio_consensus=cio)
        assert "Strong Consensus" in label
        assert "Bullish" in label
        assert "(3 Bull / 0 Neutral / 0 Bear)" in label

    def test_split_neutral_dominant(self):
        cio = {
            "type": "split", "direction": "neutral",
            "pm_count": 3, "bullish": 0, "bearish": 1, "neutral": 2,
            "confidence_floor": 30, "low_conf_pms": ["charlie_munger_agent"],
        }
        label = _consensus_label([], cio_consensus=cio)
        assert "Split" in label
        assert "Neutral" in label

    def test_soft_label_when_low_conf_present(self):
        cio = {
            "type": "soft", "direction": "bullish",
            "pm_count": 3, "bullish": 3, "bearish": 0, "neutral": 0,
            "confidence_floor": 30, "low_conf_pms": ["x_agent"],
        }
        label = _consensus_label([], cio_consensus=cio)
        assert "Soft Consensus" in label

    def test_diverged_label(self):
        cio = {
            "type": "diverged", "direction": "mixed",
            "pm_count": 3, "bullish": 1, "bearish": 1, "neutral": 1,
            "confidence_floor": 30, "low_conf_pms": [],
        }
        label = _consensus_label([], cio_consensus=cio)
        assert "Diverged" in label

    def test_none_falls_back_to_count_based(self):
        # No CIO consensus -> use old count logic (backward compat)
        label = _consensus_label([{"signal": "bullish"}] * 3, cio_consensus=None)
        assert "Strong Consensus" in label and "Bullish" in label


class TestConsensusLabel:
    def test_all_bullish(self):
        assert "全会一致 Bullish" in _consensus_label(
            [{"signal": "bullish"}, {"signal": "bullish"}, {"signal": "bullish"}]
        )

    def test_all_neutral(self):
        assert "全会一致 Neutral" in _consensus_label([{"signal": "neutral"}] * 3)

    def test_majority_bearish(self):
        label = _consensus_label([
            {"signal": "bearish"},
            {"signal": "bearish"},
            {"signal": "neutral"},
        ])
        assert "Majority Bearish" in label

    def test_split(self):
        label = _consensus_label([
            {"signal": "bullish"},
            {"signal": "neutral"},
            {"signal": "bearish"},
        ])
        assert "Split" in label

    def test_empty(self):
        assert _consensus_label([]) == "未評価"


class TestGovWarning:
    def test_short_triggers_warning(self):
        msg = _gov_warning_for_action("short")
        assert msg is not None
        assert "現物のみ" in msg

    def test_cover_triggers_warning(self):
        assert _gov_warning_for_action("cover") is not None

    def test_buy_no_warning(self):
        assert _gov_warning_for_action("buy") is None

    def test_hold_no_warning(self):
        assert _gov_warning_for_action("hold") is None


class TestHumanAgentName:
    def test_basic(self):
        assert _human_agent_name("warren_buffett_agent") == "Warren Buffett"
        assert _human_agent_name("stanley_druckenmiller_agent") == "Stanley Druckenmiller"
        assert _human_agent_name("charlie_munger_agent") == "Charlie Munger"


# ---------------- next_rec_id ----------------

class TestNextRecId:
    def test_first_id_of_day(self, tmp_path):
        rec_id = next_rec_id(tmp_path, on_date=datetime(2026, 5, 20))
        assert rec_id == "REC-20260520-001"

    def test_increments_on_existing(self, tmp_path):
        (tmp_path / "REC-20260520-001-4751.md").touch()
        (tmp_path / "REC-20260520-002-8001.md").touch()
        assert next_rec_id(tmp_path, on_date=datetime(2026, 5, 20)) == "REC-20260520-003"

    def test_ignores_other_dates(self, tmp_path):
        (tmp_path / "REC-20260101-001-AAPL.md").touch()
        (tmp_path / "REC-20260520-005-4751.md").touch()
        assert next_rec_id(tmp_path, on_date=datetime(2026, 5, 21)) == "REC-20260521-001"

    def test_creates_dir(self, tmp_path):
        sub = tmp_path / "deep" / "nested" / "dir"
        rec_id = next_rec_id(sub, on_date=datetime(2026, 5, 20))
        assert rec_id == "REC-20260520-001"
        assert sub.exists()


# ---------------- format_report ----------------

@pytest.fixture
def sample_result():
    """Mimic the dict returned by run_hedge_fund for a 3-agent run on 4751."""
    return {
        "decisions": {
            "4751": {
                "action": "hold",
                "quantity": 0,
                "confidence": 55.0,
                "reasoning": "Neutral signal prevails.",
            }
        },
        "analyst_signals": {
            "warren_buffett_agent": {
                "4751": {"signal": "neutral", "confidence": 55,
                         "reasoning": "Strong ROE and liquidity, but weak op margin. MoS unknown."}
            },
            "charlie_munger_agent": {
                "4751": {"signal": "bearish", "confidence": 30,
                         "reasoning": "Insufficient data across all critical areas."}
            },
            "stanley_druckenmiller_agent": {
                "4751": {"signal": "neutral", "confidence": 30.0,
                         "reasoning": "Severely lacking growth and risk-reward data."}
            },
            "risk_management_agent": {
                "4751": {
                    "remaining_position_limit": 74343.07,
                    "current_price": 1316.5,
                    "volatility_metrics": {
                        "daily_volatility": 0.0197,
                        "annualized_volatility": 0.313,
                    },
                    "reasoning": {
                        "portfolio_value": 500000.0,
                        "risk_adjustment": "vol-adjusted 14.9%",
                    }
                }
            }
        }
    }


class TestFormatReport:
    def test_includes_rec_id_and_ticker(self, sample_result):
        text = format_report(
            "4751", "REC-20260520-007", sample_result,
            model_name="qwen/qwen-2.5-72b-instruct",
            start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "REC-20260520-007" in text
        assert "4751" in text

    def test_pm_consensus_section(self, sample_result):
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        # Without consensus dict, falls back to count-based label
        assert "Majority Neutral" in text or "Split" in text
        assert "Warren Buffett" in text
        assert "Charlie Munger" in text
        assert "Stanley Druckenmiller" in text

    def test_format_report_renders_devils_advocate_when_present(self, sample_result):
        """When the CIO consensus dict carries a devils_advocate sub-block,
        the report must surface it with verdict + 3 counter-arguments + PM
        assessment table."""
        sample_result["consensus"] = {
            "4751": {
                "type": "strong", "direction": "bullish",
                "pm_count": 3, "bullish": 3, "bearish": 0, "neutral": 0,
                "confidence_floor": 70, "low_conf_pms": [],
                "devils_advocate": {
                    "counter_arguments": [
                        {"angle": "moat fragility",
                         "challenge": "What if AWS-style hyperscaler disrupts the ad inventory?",
                         "if_true_then": "Ad-tech margins compress 40%+"},
                        {"angle": "macro pivot",
                         "challenge": "BOJ surprise hike could re-rate growth multiples.",
                         "if_true_then": "PER compresses from 16x to 10x"},
                        {"angle": "valuation",
                         "challenge": "Current PBR 3.5x prices in 5 years of perfect execution.",
                         "if_true_then": "Margin of safety effectively zero"},
                    ],
                    "pm_assessments": [
                        {"agent": "warren_buffett_agent", "is_likely_to_hold": True,
                         "confidence_change_estimate": -5, "rationale": "DCF accommodates -40% margin shock"},
                        {"agent": "charlie_munger_agent", "is_likely_to_hold": False,
                         "confidence_change_estimate": -25, "rationale": "Has not explicitly modeled hyperscaler entry"},
                    ],
                    "consensus_survives": True,
                    "survival_rationale": "Majority of PMs hold; deltas stay within tolerance.",
                    "recommended_next_step": "proceed",
                },
            }
        }
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "Devil's Advocate" in text
        assert "Strong Consensus 確定" in text  # consensus_survives=True
        assert "Majority of PMs hold" in text  # survival_rationale
        assert "proceed" in text  # recommended_next_step
        # All 3 counter-arguments rendered with their angle labels
        assert "moat fragility" in text
        assert "macro pivot" in text
        assert "valuation" in text
        # PM assessment table includes both PMs with their delta
        assert "Warren Buffett" in text
        assert "Charlie Munger" in text
        assert "-5" in text  # delta formatted
        assert "-25" in text

    def test_format_report_da_failure_triggers_round2_label(self, sample_result):
        """When consensus_survives is False the report shows 'Round 2 要請'."""
        sample_result["consensus"] = {
            "4751": {
                "type": "strong", "direction": "bullish",
                "pm_count": 3, "bullish": 3, "bearish": 0, "neutral": 0,
                "confidence_floor": 70, "low_conf_pms": [],
                "devils_advocate": {
                    "counter_arguments": [
                        {"angle": "x", "challenge": "c", "if_true_then": "t"},
                    ] * 3,
                    "pm_assessments": [],
                    "consensus_survives": False,
                    "survival_rationale": "Buffett would lose 40 conf points.",
                    "recommended_next_step": "trigger_round2",
                },
            }
        }
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "Round 2 要請" in text
        assert "trigger_round2" in text

    def test_format_report_renders_round2_qa_section(self, sample_result):
        """When round2a / round2b are present in the result, the report must
        surface a '2.5 Round 2 Q&A' section with Q -> A threads."""
        sample_result["round2a"] = {
            "4751": {
                "warren_buffett_agent": {
                    "questions": [
                        {"target": "charlie_munger_agent",
                         "question": "Which moat metric is insufficient?",
                         "rationale": "Munger flagged moat data"}
                    ],
                    "self_note": "",
                }
            }
        }
        sample_result["round2b"] = {
            "4751": {
                "charlie_munger_agent": {
                    "answers": [
                        {"asker": "warren_buffett_agent",
                         "question": "Which moat metric is insufficient?",
                         "answer": "ROIC 10-year consistency, EDINET data missing.",
                         "view_changed": "partial",
                         "change_note": "lower confidence by 10"}
                    ]
                }
            }
        }
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "Round 2 Q&A" in text
        # Asker -> target labels
        assert "Warren Buffett からの質問" in text
        assert "→ Charlie Munger" in text
        # Question and answer text both present
        assert "Which moat metric is insufficient" in text
        assert "ROIC 10-year consistency" in text
        # view_changed label rendered as the human label
        assert "🔁 部分変更" in text
        # change_note shown for partial
        assert "lower confidence by 10" in text
        # Summary count line present
        assert "回答ラベル集計" in text

    def test_format_report_no_round2_section_when_absent(self, sample_result):
        """If round2a/round2b are missing from the result, no section appears."""
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "Round 2 Q&A" not in text

    def test_format_report_no_da_section_when_consensus_not_strong(self, sample_result):
        """Non-strong consensus must NOT have a Devil's Advocate section even
        if a stale devils_advocate payload happens to be present (defense in
        depth against state bleed between runs / cached fixtures)."""
        sample_result["consensus"] = {
            "4751": {
                "type": "split", "direction": "neutral",
                "pm_count": 3, "bullish": 0, "bearish": 1, "neutral": 2,
                "confidence_floor": 30, "low_conf_pms": [],
                # Stale DA payload: must be suppressed by the gate
                "devils_advocate": {
                    "counter_arguments": [
                        {"angle": "x", "challenge": "stale", "if_true_then": "t"}
                    ] * 3,
                    "pm_assessments": [
                        {"agent": "warren_buffett_agent", "is_likely_to_hold": True,
                         "confidence_change_estimate": 0, "rationale": "stale"},
                    ],
                    "consensus_survives": True,
                    "survival_rationale": "stale payload — must not render",
                    "recommended_next_step": "proceed",
                },
            }
        }
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "Devil's Advocate" not in text
        assert "stale" not in text
        assert "Strong Consensus 確定" not in text

    def test_format_report_da_table_handles_float_delta(self, sample_result):
        """A float confidence_change_estimate must format without raising
        TypeError (`:+d` would crash on float; we use `:+g` instead)."""
        sample_result["consensus"] = {
            "4751": {
                "type": "strong", "direction": "bullish",
                "pm_count": 3, "bullish": 3, "bearish": 0, "neutral": 0,
                "confidence_floor": 70, "low_conf_pms": [],
                "devils_advocate": {
                    "counter_arguments": [
                        {"angle": "x", "challenge": "c", "if_true_then": "t"}
                    ] * 3,
                    "pm_assessments": [
                        {"agent": "warren_buffett_agent", "is_likely_to_hold": True,
                         "confidence_change_estimate": -5.5, "rationale": "float delta"},
                        {"agent": "charlie_munger_agent", "is_likely_to_hold": False,
                         "confidence_change_estimate": 12.0, "rationale": "another float"},
                    ],
                    "consensus_survives": True,
                    "survival_rationale": "ok",
                    "recommended_next_step": "proceed",
                },
            }
        }
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        # `:+g` renders -5.5 as "-5.5" and 12.0 as "+12"
        assert "-5.5" in text
        assert "+12" in text

    def test_format_report_uses_cio_consensus_when_present(self, sample_result):
        """When result['consensus'][ticker] exists, label reflects CIO classification."""
        from src.agents.cio_consensus import CONFIDENCE_FLOOR

        sample_result["consensus"] = {
            "4751": {
                "type": "split", "direction": "neutral",
                "pm_count": 3, "bullish": 0, "bearish": 1, "neutral": 2,
                "confidence_floor": 30, "low_conf_pms": ["charlie_munger_agent"],
            }
        }
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "Split" in text
        assert "CIO Round 1.5" in text  # section header
        assert "最低確信度" in text
        assert "30%" in text
        assert "Charlie Munger" in text  # listed as low-conf PM
        # Threshold label uses the canonical CONFIDENCE_FLOOR constant — guards
        # against silent drift between the classifier and the report.
        assert f"低確信 PM (< {CONFIDENCE_FLOOR}%)" in text

    def test_risk_check_table(self, sample_result):
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "リスク評価" in text
        assert "1.97%" in text  # daily vol
        assert "31.3%" in text  # annual vol
        assert "信用空売り" in text

    def test_short_action_includes_warning(self, sample_result):
        sample_result["decisions"]["4751"]["action"] = "short"
        sample_result["decisions"]["4751"]["quantity"] = 56
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "ガバナンス警告" in text
        assert "現物のみ" in text

    def test_no_warning_for_hold(self, sample_result):
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "ガバナンス警告" not in text

    def test_shareholder_decision_section(self, sample_result):
        text = format_report(
            "4751", "REC-X", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        assert "株主決定事項" in text
        assert "推奨を承認" in text


class TestSaveAndEmitReports:
    def test_save_report_writes_utf8(self, tmp_path, sample_result):
        text = format_report(
            "4751", "REC-20260520-001", sample_result,
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
        )
        p = save_report(text, "REC-20260520-001", "4751", tmp_path)
        assert p.exists()
        assert p.read_text(encoding="utf-8") == text

    def test_emit_reports_writes_one_per_ticker(self, tmp_path):
        result = {
            "decisions": {
                "4751": {"action": "hold", "quantity": 0, "confidence": 55, "reasoning": "x"},
                "8001": {"action": "hold", "quantity": 0, "confidence": 50, "reasoning": "y"},
            },
            "analyst_signals": {
                "warren_buffett_agent": {
                    "4751": {"signal": "neutral", "confidence": 55, "reasoning": "a"},
                    "8001": {"signal": "neutral", "confidence": 50, "reasoning": "b"},
                }
            }
        }
        paths = emit_reports_for_run(
            result, tickers=["4751", "8001"],
            model_name="m", start_date="2026-04-01", end_date="2026-05-15",
            output_dir=tmp_path,
        )
        assert len(paths) == 2
        # Filenames are monotonic per day
        names = sorted(p.name for p in paths)
        assert names[0].startswith("REC-")
        assert "4751" in names[0]
        assert "8001" in names[1]


# ---------------- Watchlist updater ----------------

class TestSignalSummary:
    def test_mixed(self):
        s = _signal_summary([{"signal": "bullish"}, {"signal": "neutral"}, {"signal": "bearish"}])
        assert "Bullish 1" in s
        assert "Neutral 1" in s
        assert "Bearish 1" in s

    def test_empty(self):
        assert _signal_summary([]) == "no PM signals"


class TestDecideStatus:
    def test_buy_returns_none(self):
        # Buy = active position, not watch
        assert _decide_status("buy", []) is None

    def test_hold_returns_watch(self):
        assert _decide_status("hold", []) == "watch"

    def test_short_returns_watch(self):
        assert _decide_status("short", []) == "watch"


class TestAppendOrUpdate:
    def test_creates_new_file_and_entry(self, tmp_path):
        path = tmp_path / "watchlist.yaml"
        append_or_update(
            "4751", rec_id="REC-20260520-001", action="hold",
            pm_signals=[{"signal": "neutral"}, {"signal": "bearish"}, {"signal": "neutral"}],
            pm_reasoning="test reason",
            on_date=date(2026, 5, 20),
            watchlist_path=path,
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["watchlist"]) == 1
        entry = data["watchlist"][0]
        assert entry["ticker"] == "4751"
        assert entry["status"] == "watch"
        assert entry["source_recommendation_id"] == "REC-20260520-001"
        assert len(entry["review_history"]) == 1

    def test_buy_skips_file_write(self, tmp_path):
        """Buy decisions should not be tracked in watchlist."""
        path = tmp_path / "watchlist.yaml"
        append_or_update(
            "4751", rec_id="REC-X", action="buy",
            pm_signals=[{"signal": "bullish"}],
            pm_reasoning="r",
            watchlist_path=path,
        )
        # File should not be created for a Buy
        assert not path.exists()

    def test_preserves_existing_rich_entry(self, tmp_path):
        """If an entry already exists with rich reentry_conditions, do NOT overwrite."""
        path = tmp_path / "watchlist.yaml"
        # Seed with a rich existing entry
        initial = {
            "version": 1,
            "watchlist": [
                {
                    "ticker": "4751",
                    "name": "サイバーエージェント",
                    "status": "pass",
                    "reentry_conditions": {
                        "buffett": {"required_all": ["条件1", "条件2"]},
                    },
                    "monitoring_events": [
                        {"event": "日銀会合", "priority": "最高"},
                    ],
                    "review_history": [
                        {"date": "2026-05-15", "outcome": "pass"},
                    ],
                }
            ]
        }
        path.write_text(yaml.safe_dump(initial, allow_unicode=True), encoding="utf-8")

        # Now run an update for the same ticker
        append_or_update(
            "4751", rec_id="REC-NEW", action="hold",
            pm_signals=[{"signal": "neutral"}],
            pm_reasoning="new",
            on_date=date(2026, 5, 20),
            watchlist_path=path,
        )

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        entry = data["watchlist"][0]
        # The rich fields MUST be preserved
        assert entry["name"] == "サイバーエージェント"
        assert entry["reentry_conditions"]["buffett"]["required_all"] == ["条件1", "条件2"]
        assert len(entry["monitoring_events"]) == 1
        # History should have been extended
        assert len(entry["review_history"]) == 2
        assert entry["review_history"][-1]["recommendation_id"] == "REC-NEW"


class TestUpdateWatchlistForRun:
    def test_handles_multiple_tickers(self, tmp_path):
        path = tmp_path / "watchlist.yaml"
        result = {
            "decisions": {
                "4751": {"action": "hold", "reasoning": "r1"},
                "8001": {"action": "buy", "reasoning": "r2"},  # buy = no watchlist entry
            },
            "analyst_signals": {
                "warren_buffett_agent": {
                    "4751": {"signal": "neutral"},
                    "8001": {"signal": "bullish"},
                }
            }
        }
        update_watchlist_for_run(
            result, tickers=["4751", "8001"],
            rec_id_map={"4751": "REC-A", "8001": "REC-B"},
            watchlist_path=path,
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        # Only 4751 should be in the list (8001 was a Buy)
        assert len(data["watchlist"]) == 1
        assert data["watchlist"][0]["ticker"] == "4751"
