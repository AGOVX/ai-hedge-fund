"""Persistent filings store — SQLite index + on-disk documents.

Downloaded disclosure documents (EDINET 有報 PDF/XBRL, TDnet 決算短信 PDF) and
extracted line-item payloads are persisted under FILINGS_DIR so they are
re-usable across processes / sessions without re-downloading.

Layout:

    <FILINGS_DIR>/
    ├── filings.db            SQLite index (filings + line_items tables)
    └── <ticker>/             one directory per ticker
        ├── <doc_id>_yuho.pdf
        └── <doc_id>_tanshin.pdf

FILINGS_DIR resolution order:
    1. env var FILINGS_DIR (tests point this at tmp dirs)
    2. <repo root>/data/filings   (repo root = E:\\Company, 3 levels above this file)

All timestamps are UTC ISO-8601 strings.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    source      TEXT NOT NULL,            -- 'edinet' | 'tdnet'
    doc_type    TEXT NOT NULL,            -- 'yuho_pdf' | 'tanshin_pdf' | ...
    doc_id      TEXT NOT NULL,            -- EDINET docID / TDnet file stem
    period      TEXT,                     -- fiscal period end (YYYY-MM-DD) if known
    title       TEXT,
    file_path   TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    UNIQUE (source, doc_id, doc_type)
);
CREATE TABLE IF NOT EXISTS line_items (
    ticker      TEXT NOT NULL,
    doc_id      TEXT NOT NULL,
    period      TEXT,
    payload     TEXT NOT NULL,            -- JSON dict of extracted line items
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (ticker, doc_id)
);
"""


def filings_dir() -> Path:
    """Resolve the filings root directory (created on demand)."""
    env = os.environ.get("FILINGS_DIR", "")
    if env:
        root = Path(env)
    else:
        # src/tools/filings_store.py -> tools -> src -> ai-hedge-fund -> <repo root>
        root = Path(__file__).resolve().parents[3] / "data" / "filings"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ticker_dir(ticker: str) -> Path:
    """Per-ticker document directory (created on demand)."""
    d = filings_dir() / ticker
    d.mkdir(parents=True, exist_ok=True)
    return d


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(filings_dir() / "filings.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Filings (downloaded documents)
# ---------------------------------------------------------------------------

def record_filing(
    ticker: str,
    source: str,
    doc_type: str,
    doc_id: str,
    file_path: str | Path,
    period: str | None = None,
    title: str | None = None,
) -> None:
    """Register (or refresh) a downloaded document in the index."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO filings (ticker, source, doc_type, doc_id, period, title, file_path, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, doc_id, doc_type) DO UPDATE SET
                file_path = excluded.file_path,
                period    = COALESCE(excluded.period, filings.period),
                title     = COALESCE(excluded.title, filings.title),
                fetched_at = excluded.fetched_at
            """,
            (ticker, source, doc_type, doc_id, period, title, str(file_path), _now_iso()),
        )


def find_filing(
    ticker: str,
    doc_type: str | None = None,
    source: str | None = None,
) -> dict | None:
    """Return the most recently fetched filing matching the filters, or None.

    Only returns a hit when the underlying file still exists on disk —
    a stale index row with a deleted file behaves as a cache miss.
    """
    q = "SELECT * FROM filings WHERE ticker = ?"
    params: list = [ticker]
    if doc_type:
        q += " AND doc_type = ?"
        params.append(doc_type)
    if source:
        q += " AND source = ?"
        params.append(source)
    q += " ORDER BY fetched_at DESC"

    with _connect() as conn:
        for row in conn.execute(q, params):
            d = dict(row)
            if Path(d["file_path"]).exists():
                return d
            logger.warning("Filing index points to missing file: %s", d["file_path"])
    return None


def list_filings(ticker: str | None = None) -> list[dict]:
    """All indexed filings (optionally for one ticker), newest first."""
    with _connect() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM filings WHERE ticker = ? ORDER BY fetched_at DESC", (ticker,)
            )
        else:
            rows = conn.execute("SELECT * FROM filings ORDER BY fetched_at DESC")
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Extracted line items (parsed XBRL payloads)
# ---------------------------------------------------------------------------

def save_line_items(ticker: str, doc_id: str, payload: dict) -> None:
    """Persist an extracted line-items dict so future runs skip re-parsing."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO line_items (ticker, doc_id, period, payload, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (ticker, doc_id) DO UPDATE SET
                payload = excluded.payload,
                period  = excluded.period,
                fetched_at = excluded.fetched_at
            """,
            (ticker, doc_id, payload.get("report_period"), json.dumps(payload), _now_iso()),
        )


def load_line_items(ticker: str, max_age_days: int = 30) -> dict | None:
    """Return the freshest cached line-items payload for a ticker, or None.

    max_age_days bounds how long we trust the cache before re-checking EDINET
    for a newer filing (annual reports appear ~once a year, so 30 days is
    cheap insurance, not a correctness requirement).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT payload FROM line_items
            WHERE ticker = ? AND fetched_at >= ?
            ORDER BY fetched_at DESC LIMIT 1
            """,
            (ticker, cutoff),
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Corrupt cached line_items for %s: %s", ticker, e)
        return None
