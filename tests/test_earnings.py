"""Unit tests for src/tools/earnings.py — earnings calendar (hybrid)."""
from datetime import date, datetime

from src.tools.earnings import (
    _coerce_date,
    earnings_alert,
    resolve_earnings_date,
)


class TestCoerceDate:
    def test_none(self):
        assert _coerce_date(None) is None

    def test_datetime_first(self):
        # datetime は date のサブクラス — datetime 判定が先でないと date() を失う
        assert _coerce_date(datetime(2026, 8, 5, 15, 0)) == date(2026, 8, 5)

    def test_date_passthrough(self):
        assert _coerce_date(date(2026, 8, 5)) == date(2026, 8, 5)

    def test_iso_string(self):
        assert _coerce_date("2026-08-05") == date(2026, 8, 5)

    def test_iso_string_with_time(self):
        assert _coerce_date("2026-08-05T15:00:00") == date(2026, 8, 5)

    def test_garbage(self):
        assert _coerce_date("not-a-date") is None


class TestResolveEarningsDate:
    TODAY = date(2026, 6, 10)

    def test_manual_takes_priority_over_yf(self):
        entry = {"ticker": "4751", "earnings_date": "2026-08-05"}
        called = []

        def yf_stub(ticker):
            called.append(ticker)
            return date(2026, 7, 1)

        out = resolve_earnings_date(entry, self.TODAY, yf_fetch=yf_stub)
        assert out == {"date": "2026-08-05", "source": "手動", "days_to": 56}
        assert called == []  # yfinance には触らない

    def test_yf_fallback_when_no_manual(self):
        entry = {"ticker": "4751"}
        out = resolve_earnings_date(entry, self.TODAY, yf_fetch=lambda t: date(2026, 6, 15))
        assert out == {"date": "2026-06-15", "source": "推定", "days_to": 5}

    def test_yf_returns_none(self):
        entry = {"ticker": "4751"}
        out = resolve_earnings_date(entry, self.TODAY, yf_fetch=lambda t: None)
        assert out == {"date": None, "source": None, "days_to": None}

    def test_manual_past_date_kept(self):
        # 過去日の手動記入はそのまま返す (更新を促すアラートにつながる)
        entry = {"ticker": "4751", "earnings_date": "2026-05-15"}
        out = resolve_earnings_date(entry, self.TODAY, yf_fetch=lambda t: None)
        assert out["date"] == "2026-05-15"
        assert out["source"] == "手動"
        assert out["days_to"] == -26

    def test_yaml_date_object(self):
        # yaml.safe_load は日付リテラルを date にする
        entry = {"ticker": "4751", "earnings_date": date(2026, 8, 5)}
        out = resolve_earnings_date(entry, self.TODAY)
        assert out["date"] == "2026-08-05"


class TestEarningsAlert:
    def test_none_info(self):
        assert earnings_alert({"date": None, "source": None, "days_to": None}) is None

    def test_far_future_no_alert(self):
        assert earnings_alert({"date": "2026-08-05", "source": "手動", "days_to": 56}) is None

    def test_within_window(self):
        out = earnings_alert({"date": "2026-06-15", "source": "推定", "days_to": 5})
        assert out is not None
        assert "決算発表接近" in out
        assert "残り5日" in out
        assert "推定" in out

    def test_boundary_exactly_7_days(self):
        assert earnings_alert({"date": "2026-06-17", "source": "手動", "days_to": 7}) is not None
        assert earnings_alert({"date": "2026-06-18", "source": "手動", "days_to": 8}) is None

    def test_today(self):
        out = earnings_alert({"date": "2026-06-10", "source": "手動", "days_to": 0})
        assert out is not None
        assert "残り0日" in out

    def test_past_date_prompts_update(self):
        out = earnings_alert({"date": "2026-05-15", "source": "手動", "days_to": -26})
        assert out is not None
        assert "過去" in out
        assert "更新を推奨" in out
