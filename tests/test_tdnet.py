"""Unit tests for src/tools/tdnet.py — all network calls mocked."""
from unittest.mock import MagicMock, patch

import pytest

from src.tools import filings_store, tdnet


@pytest.fixture(autouse=True)
def _isolated_filings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FILINGS_DIR", str(tmp_path / "filings"))


def _list_response(items):
    resp = MagicMock()
    resp.json.return_value = {"items": items}
    resp.raise_for_status.return_value = None
    return resp


def _pdf_response(content=b"%PDF-1.7 fake-tanshin"):
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status.return_value = None
    return resp


_ITEMS = [
    {"Tdnet": {
        "title": "2026年3月期 決算短信〔日本基準〕(連結)",
        "document_url": "https://www.release.tdnet.info/inbs/140120260510099999.pdf",
        "company_code": "47510",
        "pubdate": "2026-05-10 15:00:00",
    }},
    {"Tdnet": {
        "title": "配当予想の修正に関するお知らせ",
        "document_url": "https://www.release.tdnet.info/inbs/140120260401088888.pdf",
        "company_code": "47510",
        "pubdate": "2026-04-01 15:00:00",
    }},
]


class TestListDisclosures:
    @patch("httpx.get")
    def test_normalizes_items(self, mock_get):
        mock_get.return_value = _list_response(_ITEMS)
        out = tdnet.list_disclosures("4751")
        assert len(out) == 2
        assert out[0]["doc_id"] == "140120260510099999"
        assert "決算短信" in out[0]["title"]

    @patch("httpx.get")
    def test_filters_other_companies(self, mock_get):
        items = _ITEMS + [{"Tdnet": {
            "title": "決算短信 (他社)",
            "document_url": "https://example.com/x.pdf",
            "company_code": "99990",
        }}]
        mock_get.return_value = _list_response(items)
        out = tdnet.list_disclosures("4751")
        assert all(d["company_code"].startswith("4751") for d in out)

    @patch("httpx.get", side_effect=Exception("network down"))
    def test_network_failure_returns_empty(self, mock_get):
        assert tdnet.list_disclosures("4751") == []


class TestDownloadKessanTanshin:
    @patch("httpx.get")
    def test_downloads_and_records(self, mock_get):
        mock_get.side_effect = [_list_response(_ITEMS), _pdf_response()]
        path = tdnet.download_kessan_tanshin("4751")

        assert path is not None
        assert path.exists()
        assert path.read_bytes().startswith(b"%PDF")
        hit = filings_store.find_filing("4751", doc_type="tanshin_pdf", source="tdnet")
        assert hit["doc_id"] == "140120260510099999"

    @patch("httpx.get")
    def test_second_call_is_cache_hit(self, mock_get):
        mock_get.side_effect = [_list_response(_ITEMS), _pdf_response(), _list_response(_ITEMS)]
        first = tdnet.download_kessan_tanshin("4751")
        second = tdnet.download_kessan_tanshin("4751")

        assert first == second
        # 3 calls total: list + pdf (1st run), list only (2nd run — no PDF download)
        assert mock_get.call_count == 3

    @patch("httpx.get")
    def test_no_tanshin_in_listing(self, mock_get):
        mock_get.return_value = _list_response([_ITEMS[1]])  # 配当修正のみ
        assert tdnet.download_kessan_tanshin("4751") is None

    @patch("httpx.get")
    def test_listing_down_falls_back_to_cache(self, mock_get):
        mock_get.side_effect = [_list_response(_ITEMS), _pdf_response(), Exception("down")]
        first = tdnet.download_kessan_tanshin("4751")
        second = tdnet.download_kessan_tanshin("4751")
        assert second == first

    @patch("httpx.get")
    def test_non_pdf_content_discarded(self, mock_get):
        mock_get.side_effect = [_list_response(_ITEMS), _pdf_response(b"<html>error</html>")]
        assert tdnet.download_kessan_tanshin("4751") is None
