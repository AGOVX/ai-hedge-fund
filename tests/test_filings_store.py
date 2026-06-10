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
