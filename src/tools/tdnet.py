"""TDnet disclosure fetcher — downloads 決算短信 PDFs with persistent caching.

TDnet (適時開示情報閲覧サービス) has no official API, so this module uses the
yanoshin TDnet Web API (https://webapi.yanoshin.jp/tdnet/) — a free JSON mirror
of TDnet disclosure listings — to find recent filings, then downloads the PDF
directly from TDnet. Access pattern is one ticker at a time, on demand, so load
is negligible.

Downloaded PDFs are stored under filings_store.ticker_dir() and indexed in
SQLite; a second request for the same document is served from disk without any
network access.

No API key required.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.tools import filings_store

logger = logging.getLogger(__name__)

_LIST_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit={limit}"
_TANSHIN_RE = re.compile(r"決算短信")
_TIMEOUT = 30.0
_UA = {"User-Agent": "Mozilla/5.0 (filings-fetcher; personal research; low-frequency)"}


def list_disclosures(ticker: str, limit: int = 30) -> list[dict]:
    """Recent TDnet disclosures for a 4-digit ticker, newest first.

    Returns normalized dicts: {doc_id, title, url, pubdate, company_code}.
    Network/parse failures degrade to [] with a warning (pipeline keeps going).
    """
    import httpx

    url = _LIST_URL.format(code=ticker, limit=limit)
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, headers=_UA, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("TDnet list fetch failed for %s: %s", ticker, e)
        return []

    out: list[dict] = []
    for item in data.get("items", []):
        td = item.get("Tdnet", item) if isinstance(item, dict) else {}
        doc_url = td.get("document_url") or ""
        if not doc_url:
            continue
        # company_code in TDnet is 5-digit (ticker + check digit slot) — match prefix
        company_code = str(td.get("company_code", ""))
        if company_code and not company_code.startswith(ticker):
            continue
        out.append(
            {
                "doc_id": Path(doc_url).stem or td.get("id", ""),
                "title": td.get("title", ""),
                "url": doc_url,
                "pubdate": td.get("pubdate", ""),
                "company_code": company_code,
            }
        )
    return out


def download_kessan_tanshin(ticker: str, limit: int = 30) -> Path | None:
    """Download the latest 決算短信 PDF for a ticker; reuse the cached copy if present.

    Returns the local PDF path, or None when nothing was found / all attempts failed.
    """
    disclosures = list_disclosures(ticker, limit=limit)
    tanshin = [d for d in disclosures if _TANSHIN_RE.search(d["title"])]
    if not tanshin:
        # Even if listing failed, an earlier download may still serve
        cached = filings_store.find_filing(ticker, doc_type="tanshin_pdf", source="tdnet")
        if cached:
            logger.info("TDnet listing empty for %s — serving cached 決算短信: %s", ticker, cached["file_path"])
            return Path(cached["file_path"])
        logger.warning("No 決算短信 found on TDnet for %s (checked %d disclosures)", ticker, len(disclosures))
        return None

    latest = tanshin[0]  # listing is newest-first

    # Cache hit: same doc already on disk → no re-download
    cached = filings_store.find_filing(ticker, doc_type="tanshin_pdf", source="tdnet")
    if cached and cached["doc_id"] == latest["doc_id"]:
        logger.info("決算短信 cache hit for %s: %s", ticker, cached["file_path"])
        return Path(cached["file_path"])

    path = _download_pdf(latest["url"], filings_store.ticker_dir(ticker) / f"{latest['doc_id']}_tanshin.pdf")
    if path is None:
        return Path(cached["file_path"]) if cached else None

    filings_store.record_filing(
        ticker=ticker,
        source="tdnet",
        doc_type="tanshin_pdf",
        doc_id=latest["doc_id"],
        file_path=path,
        title=latest["title"],
    )
    logger.info("Downloaded 決算短信 for %s: %s", ticker, path)
    return path


def _download_pdf(url: str, dest: Path) -> Path | None:
    """Stream a PDF to dest. Returns dest or None on failure."""
    import httpx

    try:
        resp = httpx.get(url, timeout=_TIMEOUT, headers=_UA, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("PDF download failed (%s): %s", url, e)
        return None

    content = resp.content
    if not content.startswith(b"%PDF"):
        logger.warning("Downloaded content is not a PDF (%s) — discarding", url)
        return None

    dest.write_bytes(content)
    return dest
