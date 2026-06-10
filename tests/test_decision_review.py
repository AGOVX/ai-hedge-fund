"""Unit tests for src/tools/decision_review.py — pure scoring logic."""
from datetime import date

from src.tools.decision_review import (
    build_scorecard,
    collect_decisions,
    evaluate_decision,
    period_return_pct,
)

TODAY = date(2026, 6, 10)


class TestEvaluateDecision:
    def test_pass_correct_when_underperforms(self):
        r = evaluate_decision("pass", stock_ret_pct=-5.0, bench_ret_pct=2.0)
        assert r["icon"] == "✅"
        assert r["excess_pt"] == -7.0

    def test_pass_ok_small_outperformance(self):
        r = evaluate_decision("pass", 8.0, 2.0)
        assert r["icon"] == "🟡"

    def test_pass_opportunity_cost(self):
        r = evaluate_decision("pass", 25.0, 2.0)
        assert r["icon"] == "❌"
        assert "機会損失" in r["verdict"]

    def test_watch_scored_same_as_pass(self):
        assert evaluate_decision("watch", -1.0, 1.0)["icon"] == "✅"

    def test_boundary_exactly_threshold(self):
        # 超過ちょうど +10pt は 🟡 (閾値は「超」)
        assert evaluate_decision("pass", 12.0, 2.0)["icon"] == "🟡"

    def test_unknown_outcome(self):
        assert evaluate_decision("buy", 5.0, 2.0)["icon"] == "❓"


class TestPeriodReturn:
    _SERIES = [("2026-05-12", 100.0), ("2026-05-13", 102.0), ("2026-06-09", 110.0)]

    def test_from_decision_date(self):
        assert period_return_pct(self._SERIES, date(2026, 5, 13)) == (110 - 102) / 102 * 100

    def test_decision_on_non_trading_day_uses_next_bar(self):
        # 5/12が判断日でなくても、5/12以降最初のバーが基準になる
        assert period_return_pct(self._SERIES, date(2026, 5, 11)) == 10.0

    def test_decision_after_last_bar(self):
        assert period_return_pct(self._SERIES, date(2026, 7, 1)) is None

    def test_empty(self):
        assert period_return_pct([], date(2026, 5, 1)) is None


class TestCollectDecisions:
    def test_flattens_history(self):
        entries = [
            {"ticker": "4751", "name": "CA", "review_history": [
                {"date": "2026-05-13", "outcome": "pass", "recommendation_id": "REC-1"},
                {"date": date(2026, 5, 15), "outcome": "pass", "recommendation_id": "REC-2"},
            ]},
            {"ticker": "8001", "name": "伊藤忠", "review_history": [
                {"date": "2026-05-13", "outcome": "watch", "recommendation_id": "REC-3"},
            ]},
            {"ticker": "9999", "name": "履歴なし"},
        ]
        out = collect_decisions(entries)
        assert len(out) == 3
        assert out[1]["decision_date"] == "2026-05-15"  # date object も文字列化される


class TestBuildScorecard:
    def test_renders_table_and_summary(self):
        decisions = [
            {"ticker": "4751", "name": "CA", "decision_date": "2026-05-13",
             "outcome": "pass", "recommendation_id": "REC-1"},
            {"ticker": "8001", "name": "伊藤忠", "decision_date": "2026-05-13",
             "outcome": "watch", "recommendation_id": "REC-3"},
        ]
        results = [
            evaluate_decision("pass", -3.0, 1.5),
            None,  # データ不足
        ]
        card = build_scorecard(decisions, results, TODAY)
        assert "✅ 1" in card
        assert "データ不足" in card
        assert "2026-06-10" in card
