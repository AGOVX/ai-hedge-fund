"""Unit tests for src/tools/filings_store.py — persistent filings index."""
import time

import pytest

from src.tools import filings_store


@pytest.fixture(autouse=True)
def _isolated_filings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FILINGS_DIR", str(tmp_path / "filings"))


def _touch(ticker: str, name: str) -> str:
    p = filings_store.ticker_dir(ticker) / name
    p.write_bytes(b"%PDF-1.7 dummy")
    return str(p)


class TestFilings:
    def test_record_and_find(self):
        path = _touch("4751", "DOC1_tanshin.pdf")
        filings_store.record_filing("4751", "tdnet", "tanshin_pdf", "DOC1", path, title="決算短信")

        hit = filings_store.find_filing("4751", doc_type="tanshin_pdf")
        assert hit is not None
        assert hit["doc_id"] == "DOC1"
        assert hit["file_path"] == path
        assert hit["title"] == "決算短信"

    def test_find_filters_by_source_and_type(self):
        p1 = _touch("4751", "A_tanshin.pdf")
        p2 = _touch("4751", "B_yuho.pdf")
        filings_store.record_filing("4751", "tdnet", "tanshin_pdf", "A", p1)
        filings_store.record_filing("4751", "edinet", "yuho_pdf", "B", p2)

        assert filings_store.find_filing("4751", doc_type="yuho_pdf")["doc_id"] == "B"
        assert filings_store.find_filing("4751", source="tdnet")["doc_id"] == "A"
        assert filings_store.find_filing("9999") is None

    def test_missing_file_is_cache_miss(self):
        filings_store.record_filing("4751", "tdnet", "tanshin_pdf", "GONE", "Z:/nonexistent/file.pdf")
        assert filings_store.find_filing("4751", doc_type="tanshin_pdf") is None

    def test_upsert_refreshes_path(self):
        p1 = _touch("4751", "old.pdf")
        filings_store.record_filing("4751", "tdnet", "tanshin_pdf", "X", p1)
        p2 = _touch("4751", "new.pdf")
        filings_store.record_filing("4751", "tdnet", "tanshin_pdf", "X", p2)

        rows = filings_store.list_filings("4751")
        assert len(rows) == 1
        assert rows[0]["file_path"] == p2

    def test_list_all(self):
        filings_store.record_filing("4751", "tdnet", "tanshin_pdf", "A", _touch("4751", "a.pdf"))
        filings_store.record_filing("8001", "tdnet", "tanshin_pdf", "B", _touch("8001", "b.pdf"))
        assert {r["ticker"] for r in filings_store.list_filings()} == {"4751", "8001"}


class TestLineItems:
    def test_save_and_load(self):
        payload = {"revenue": 720.0, "net_income": 30.0, "report_period": "2025-09-30"}
        filings_store.save_line_items("4751", "DOC1", payload)

        out = filings_store.load_line_items("4751")
        assert out == payload

    def test_load_respects_max_age(self):
        filings_store.save_line_items("4751", "DOC1", {"revenue": 1.0, "report_period": None})
        time.sleep(0.01)
        assert filings_store.load_line_items("4751", max_age_days=0) is None
        assert filings_store.load_line_items("4751", max_age_days=30) is not None

    def test_load_unknown_ticker(self):
        assert filings_store.load_line_items("0000") is None

    def test_upsert_same_doc_id(self):
        filings_store.save_line_items("4751", "DOC1", {"revenue": 1.0, "report_period": None})
        filings_store.save_line_items("4751", "DOC1", {"revenue": 2.0, "report_period": None})
        assert filings_store.load_line_items("4751")["revenue"] == 2.0

    def test_latest_period_not_latest_fetched(self):
        # 回帰: --history が過去期を後から保存すると最古期が最新 fetched_at になる。
        # 最新期 (2025-09-30) を先に、過去期を後に保存しても、最新会計期を返すこと。
        filings_store.save_line_items("4751", "NEW", {"revenue": 874.0, "report_period": "2025-09-30"})
        time.sleep(0.01)
        filings_store.save_line_items("4751", "OLD", {"revenue": 311.0, "report_period": "2016-09-30"})
        out = filings_store.load_line_items("4751")
        assert out["report_period"] == "2025-09-30"
        assert out["revenue"] == 874.0

    def test_real_period_beats_null_period(self):
        # report_period 不明 (NULL) の行より、期末日のある行を優先する
        filings_store.save_line_items("4751", "DATED", {"revenue": 874.0, "report_period": "2025-09-30"})
        time.sleep(0.01)
        filings_store.save_line_items("4751", "UNDATED", {"revenue": 1.0, "report_period": None})
        assert filings_store.load_line_items("4751")["report_period"] == "2025-09-30"


class TestLineItemsHistory:
    def test_history_sorted_desc_no_ttl(self):
        for i, period in enumerate(["2023-03-31", "2025-03-31", "2024-03-31"]):
            filings_store.save_line_items(
                "8001", f"DOC{i}", {"net_income": float(i), "report_period": period}
            )
        out = filings_store.load_line_items_history("8001")
        assert [p["report_period"] for p in out] == ["2025-03-31", "2024-03-31", "2023-03-31"]

    def test_same_period_deduped_latest_wins(self):
        filings_store.save_line_items("8001", "OLD", {"net_income": 1.0, "report_period": "2025-03-31"})
        time.sleep(0.01)
        filings_store.save_line_items("8001", "NEW", {"net_income": 2.0, "report_period": "2025-03-31"})
        out = filings_store.load_line_items_history("8001")
        assert len(out) == 1
        assert out[0]["net_income"] == 2.0

    def test_marker_rows_excluded(self):
        # "__" 始まりの doc_id は内部マーカー — 履歴にも最新キャッシュにも出ない
        filings_store.save_line_items("8001", "__history_scan__", {"scanned_periods": 10, "report_period": None})
        assert filings_store.load_line_items_history("8001") == []
        assert filings_store.load_line_items("8001") is None
        # マーカー自体は doc_id 指定で読める
        m = filings_store.load_line_items_by_doc("8001", "__history_scan__")
        assert m["scanned_periods"] == 10

    def test_load_by_doc_unknown(self):
        assert filings_store.load_line_items_by_doc("8001", "NOPE") is None


class TestInterimItems:
    def test_save_and_load_latest(self):
        filings_store.save_interim_items(
            "4751", "DOCH1", {"report_period": "2026-03-31", "period_type": "HY", "revenue": 478584e6}
        )
        out = filings_store.load_latest_interim("4751")
        assert out["report_period"] == "2026-03-31"
        assert out["revenue"] == 478584e6

    def test_latest_period_wins(self):
        filings_store.save_interim_items("4751", "OLD", {"report_period": "2025-03-31", "revenue": 1.0})
        time.sleep(0.01)
        filings_store.save_interim_items("4751", "NEW", {"report_period": "2026-03-31", "revenue": 2.0})
        assert filings_store.load_latest_interim("4751")["report_period"] == "2026-03-31"

    def test_unknown_ticker(self):
        assert filings_store.load_latest_interim("0000") is None

    def test_isolated_from_line_items(self):
        # 中間データは年次 line_items / history を一切汚染しない
        filings_store.save_interim_items("4751", "DOCH1", {"report_period": "2026-03-31", "revenue": 1.0})
        filings_store.save_line_items("4751", "FY", {"report_period": "2025-09-30", "revenue": 9.0})
        assert filings_store.load_line_items("4751")["report_period"] == "2025-09-30"
        assert filings_store.load_line_items_history("4751") == [
            {"report_period": "2025-09-30", "revenue": 9.0}
        ]
