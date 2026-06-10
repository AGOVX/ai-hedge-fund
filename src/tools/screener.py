"""割安株スクリーニング — universe 定義 + ¥500k 制約 + バリュー指標の一次フィルタ.

docs/gap-analysis.md「C. 割安株スクリーニング」対応。階層は:

    本モジュール (決定論的・定量一次フィルタ)
        → Top N 候補リスト (markdown / yaml)
            → 円卓討議 (CIO 起動は株主判断)

LLM は使わない (モデル割当方針のとおり、将来 Haiku 層を挟む場合も
この定量フィルタが土台になる)。

データ源:
  - 銘柄リスト: JPX 上場銘柄一覧 (data_j.xls) → data/universe/jpx-prime.csv にキャッシュ
  - 株価/指標: yfinance (PER/PBR/配当利回り/時価総額)

フィルタ (data/universe/default.yaml + Watch List 由来の ¥500k フィルタ):
  1. 東証プライム / 除外セクター (銀行・証券・保険・その他金融)
  2. 最低売買単位フィルタ: 100株 × 株価 ≤ 総資本の10% (Druckenmiller 提案)
  3. 時価総額 ≥ 100億円
  4. バリュー複合スコア: PER (低いほど良)・PBR (低いほど良)・配当利回り (高いほど良)

Usage:
    python -m src.tools.screener --refresh-universe   # JPX リスト更新 (月1程度)
    python -m src.tools.screener                      # スクリーニング実行
    python -m src.tools.screener --capital 1320000 --top 20
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import date
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_JPX_XLS_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
_PRIME_LABEL = "プライム"
_DEFAULT_CAPITAL = 500_000
_LOT_SIZE = 100
_LOT_RATIO_MAX = 0.10  # 最低売買単位 ≤ 総資本の10%


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def universe_csv_path() -> Path:
    env = os.environ.get("UNIVERSE_CSV_PATH", "")
    return Path(env) if env else repo_root() / "data" / "universe" / "jpx-prime.csv"


def load_universe_config() -> dict:
    p = repo_root() / "data" / "universe" / "default.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Universe refresh (JPX listed-companies master)
# ---------------------------------------------------------------------------

def refresh_universe() -> Path:
    """Download the JPX listing master and cache Prime tickers as CSV."""
    import httpx
    import pandas as pd

    logger.info("Downloading JPX listing master ...")
    resp = httpx.get(_JPX_XLS_URL, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()

    import io

    df = pd.read_excel(io.BytesIO(resp.content))  # columns: コード, 銘柄名, 市場・商品区分, 33業種区分, ...
    df.columns = [str(c).strip() for c in df.columns]

    prime = df[df["市場・商品区分"].astype(str).str.contains(_PRIME_LABEL, na=False)]

    cfg = load_universe_config()
    excluded = set((cfg.get("filters") or {}).get("exclude_sectors") or [])
    if excluded:
        prime = prime[~prime["33業種区分"].astype(str).isin(excluded)]

    out = universe_csv_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "sector33"])
        for _, row in prime.iterrows():
            code = str(row["コード"]).strip()
            if len(code) == 4 and code.isdigit():  # 普通株のみ (ETF/REIT等の5桁英数を除外)
                w.writerow([code, str(row["銘柄名"]).strip(), str(row["33業種区分"]).strip()])

    n = sum(1 for _ in out.open(encoding="utf-8")) - 1
    logger.info("Universe cached: %s (%d tickers)", out, n)
    return out


def load_universe_tickers() -> list[dict]:
    p = universe_csv_path()
    if not p.exists():
        logger.warning("Universe CSV not found: %s — run with --refresh-universe first", p)
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Filters & scoring (pure functions — unit-testable)
# ---------------------------------------------------------------------------

def lot_filter(price: float | None, capital: int) -> bool:
    """最低売買単位 (100株×株価) が総資本の10%以下か (Druckenmiller ¥500kフィルタ)."""
    if price is None or price <= 0:
        return False
    return price * _LOT_SIZE <= capital * _LOT_RATIO_MAX


def value_score(metrics: dict) -> float | None:
    """PER/PBR/配当利回りの複合バリュースコア (0-100, 高いほど割安).

    各指標を素点化して平均。指標が1つも取れない銘柄は None (=ランク外)。
      PER  : ≤8で満点100、25以上で0 (線形)。負値 (赤字) は0
      PBR  : ≤0.6で満点100、2.0以上で0
      配当 : ≥4.5%で満点100、0%で0
    """
    scores = []
    per = metrics.get("per")
    if per is not None:
        scores.append(0.0 if per <= 0 else max(0.0, min(100.0, (25 - per) / (25 - 8) * 100)))
    pbr = metrics.get("pbr")
    if pbr is not None and pbr > 0:
        scores.append(max(0.0, min(100.0, (2.0 - pbr) / (2.0 - 0.6) * 100)))
    dy = metrics.get("dividend_yield_pct")
    if dy is not None and dy >= 0:
        scores.append(max(0.0, min(100.0, dy / 4.5 * 100)))
    return round(sum(scores) / len(scores), 1) if scores else None


def apply_filters(rows: list[dict], capital: int, market_cap_min: float) -> list[dict]:
    """Lot + market-cap filters, then attach value_score and sort descending."""
    out = []
    for r in rows:
        if not lot_filter(r.get("price"), capital):
            continue
        mc = r.get("market_cap")
        if mc is not None and mc < market_cap_min:
            continue
        score = value_score(r)
        if score is None:
            continue
        out.append({**r, "value_score": score})
    out.sort(key=lambda x: x["value_score"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Live data fetch
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 200          # Stage 1 バッチサイズ (1リクエストの銘柄数)
_CHUNK_PAUSE_SEC = 3.0     # Stage 1 チャンク間の待機
_INFO_PAUSE_SEC = 0.4      # Stage 2 銘柄間の待機
_RATE_LIMIT_BACKOFF_SEC = 30.0


def _batch_closes(symbols: list[str]) -> dict[str, float]:
    """Chunked yf.download to avoid rate limits. Returns {symbol: latest close}."""
    import time

    import yfinance as yf

    out: dict[str, float] = {}
    for i in range(0, len(symbols), _CHUNK_SIZE):
        chunk = symbols[i:i + _CHUNK_SIZE]
        logger.info("Stage 1: chunk %d-%d / %d ...", i + 1, i + len(chunk), len(symbols))
        try:
            px = yf.download(chunk, period="5d", interval="1d", progress=False, auto_adjust=False)
        except Exception as e:
            logger.warning("chunk download failed (%s) — %ds 待機して1回だけ再試行", e, int(_RATE_LIMIT_BACKOFF_SEC))
            time.sleep(_RATE_LIMIT_BACKOFF_SEC)
            try:
                px = yf.download(chunk, period="5d", interval="1d", progress=False, auto_adjust=False)
            except Exception as e2:
                logger.error("chunk download failed twice (%s) — %d 銘柄スキップ", e2, len(chunk))
                continue
        closes = px["Close"]
        last = closes.iloc[-1] if hasattr(closes, "iloc") else closes
        for sym in chunk:
            try:
                v = float(last[sym]) if len(chunk) > 1 else float(last)
            except (KeyError, TypeError, ValueError):
                continue
            if v == v:  # not NaN
                out[sym] = v
        if i + _CHUNK_SIZE < len(symbols):
            time.sleep(_CHUNK_PAUSE_SEC)
    return out


def _fetch_info_with_retry(sym: str) -> dict:
    """Single .info fetch; on rate limit, back off once and retry."""
    import time

    import yfinance as yf

    for attempt in (1, 2):
        try:
            return yf.Ticker(sym).info or {}
        except Exception as e:
            if "Rate" in type(e).__name__ or "Too Many Requests" in str(e):
                if attempt == 1:
                    logger.warning("rate limited at %s — %ds 待機", sym, int(_RATE_LIMIT_BACKOFF_SEC))
                    time.sleep(_RATE_LIMIT_BACKOFF_SEC)
                    continue
            logger.debug("info failed for %s: %s", sym, e)
            return {}
    return {}


def fetch_metrics(tickers: list[dict], capital: int) -> tuple[list[dict], dict]:
    """Two-stage fetch: chunked batch closes for the lot filter, then .info for survivors.

    Returns (rows, coverage) — coverage は取得成功率の統計 (silent cap 防止のため
    レポートに明記する)。
    """
    import time

    symbols = [f"{t['ticker']}.T" for t in tickers]
    by_symbol = {f"{t['ticker']}.T": t for t in tickers}

    closes = _batch_closes(symbols)
    survivors = [(sym, px) for sym, px in closes.items() if lot_filter(px, capital)]

    logger.info("Stage 2: detail fetch for %d lot-filter survivors ...", len(survivors))
    rows = []
    info_ok = 0
    for n, (sym, price) in enumerate(survivors, 1):
        meta = by_symbol[sym]
        info = _fetch_info_with_retry(sym)
        if info.get("trailingPE") is not None or info.get("priceToBook") is not None:
            info_ok += 1
        dy = info.get("dividendYield")
        # yfinance returns dividendYield either as fraction (0.034) or percent (3.4) by version
        if dy is not None and dy < 1:
            dy = dy * 100
        rows.append(
            {
                "ticker": meta["ticker"],
                "name": meta["name"],
                "sector33": meta["sector33"],
                "price": price,
                "per": info.get("trailingPE"),
                "pbr": info.get("priceToBook"),
                "dividend_yield_pct": round(dy, 2) if dy is not None else None,
                "market_cap": info.get("marketCap"),
            }
        )
        if n % 20 == 0:
            logger.info("Stage 2: %d/%d ...", n, len(survivors))
        time.sleep(_INFO_PAUSE_SEC)

    coverage = {
        "universe": len(symbols),
        "price_ok": len(closes),
        "lot_survivors": len(survivors),
        "info_ok": info_ok,
    }
    return rows, coverage


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(candidates: list[dict], capital: int, top: int, today: date, universe_size: int,
                 coverage: dict | None = None) -> str:
    lines = [
        f"# 割安株スクリーニング — {today.isoformat()}",
        "",
        f"- ユニバース: 東証プライム (金融除く) {universe_size} 銘柄",
        f"- 想定総資本: ¥{capital:,} / 最低売買単位フィルタ: 100株 ≤ ¥{int(capital * _LOT_RATIO_MAX):,}"
        f" (= 株価 {int(capital * _LOT_RATIO_MAX / _LOT_SIZE):,}円以下)",
        f"- 通過: {len(candidates)} 銘柄 → Top {min(top, len(candidates))} を表示",
    ]
    if coverage:
        lines.append(
            f"- データカバレッジ: 株価 {coverage['price_ok']}/{coverage['universe']} / "
            f"単位フィルタ通過 {coverage['lot_survivors']} / 指標取得 {coverage['info_ok']}"
        )
        if coverage["price_ok"] < coverage["universe"] * 0.9 or (
            coverage["lot_survivors"] and coverage["info_ok"] < coverage["lot_survivors"] * 0.5
        ):
            lines.append("- ⚠️ **取得失敗が多い (レート制限の可能性)。時間を置いて再実行を推奨。**")
    lines += [
        "",
        "**これは定量一次フィルタであり推奨ではない。** 候補の採否は円卓討議 (CIO) → 株主承認で決める。",
        "",
        "| # | コード | 銘柄 | 業種 | 株価 | PER | PBR | 配当利回り | スコア |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(candidates[:top], 1):
        per = f"{c['per']:.1f}" if c.get("per") is not None else "—"
        pbr = f"{c['pbr']:.2f}" if c.get("pbr") is not None else "—"
        dy = f"{c['dividend_yield_pct']:.2f}%" if c.get("dividend_yield_pct") is not None else "—"
        lines.append(
            f"| {i} | {c['ticker']} | {c['name']} | {c['sector33']} | {c['price']:,.0f} | {per} | {pbr} | {dy} | {c['value_score']} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def run(capital: int = _DEFAULT_CAPITAL, top: int = 15, today: date | None = None) -> Path:
    today = today or date.today()
    universe = load_universe_tickers()
    if not universe:
        raise SystemExit("ユニバース未取得。先に --refresh-universe を実行してください。")

    cfg = load_universe_config()
    mc_min = float((cfg.get("filters") or {}).get("market_cap_min_jpy") or 10_000_000_000)

    rows, coverage = fetch_metrics(universe, capital)
    candidates = apply_filters(rows, capital, mc_min)
    report = build_report(candidates, capital, top, today, len(universe), coverage)

    out_dir = repo_root() / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"screen-{today.strftime('%Y%m%d')}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"[saved] {out_path}")
    return out_path


if __name__ == "__main__":
    import argparse
    import sys

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp932 対策
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser(description="割安株スクリーニング (定量一次フィルタ)")
    ap.add_argument("--refresh-universe", action="store_true", help="JPX 上場一覧を再取得")
    ap.add_argument("--capital", type=int, default=_DEFAULT_CAPITAL, help="想定総資本 (円)")
    ap.add_argument("--top", type=int, default=15, help="表示件数")
    args = ap.parse_args()

    if args.refresh_universe:
        refresh_universe()
    else:
        run(capital=args.capital, top=args.top)
