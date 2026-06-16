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
  5. 質的足切り (スコア上位のみ): ROE≥8% / 自己資本比率≥30% / 営業CF>0
     — バリュートラップ (割安に見えるだけの低収益・高負債) の機械的除外。
     欠損データは除外せず ⚠ フラグで残す (market_cap 不明素通りと同じ lenient 思想)

Usage:
    python -m src.tools.screener --refresh-universe   # JPX リスト更新 (月1程度)
    python -m src.tools.screener                      # スクリーニング実行
    python -m src.tools.screener --capital 1320000 --top 20
    python -m src.tools.screener --no-quality         # 質的足切りをスキップ (高速)
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import date
from pathlib import Path

import yaml

from src.tools.common import repo_root, write_report

logger = logging.getLogger(__name__)

_JPX_XLS_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
_PRIME_LABEL = "プライム"
_DEFAULT_CAPITAL = 1_991_043  # 実総資本 (real-holdings.yaml)。旧¥500k想定は2026-06-14廃止
_LOT_SIZE = 100
_LOT_RATIO_MAX = 0.10  # 最低売買単位 ≤ 総資本の10%

# 質的足切り (標準基準) — スコア上位 N 銘柄のみ (全銘柄に balance_sheet は重い)
_QUALITY_TOP_N = 50
_ROE_MIN_PCT = 8.0
_EQUITY_RATIO_MIN_PCT = 30.0


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


def _to_float(v) -> float | None:
    """yfinance .info は稀に数値を文字列('N/A'含む)で返すため安全に float 化する。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN を弾く


def effective_per(info: dict) -> float | None:
    """trailingPE。赤字企業は yfinance が PE を None で返すため、EPS<0 を検知して
    -1.0 (= value_score で素点0) に落とす — 赤字銘柄が PER ペナルティを素通りして
    PBR/配当だけで満点を取るのを防ぐ。EPS も不明なら None (データ欠落扱い)。
    文字列で返るケースがあるため _to_float で正規化する。
    """
    per = _to_float(info.get("trailingPE"))
    if per is not None:
        return per
    eps = _to_float(info.get("trailingEps"))
    if eps is not None and eps < 0:
        return -1.0
    return None


def dividend_yield_pct(info: dict, price: float | None) -> float | None:
    """配当利回り (%) を曖昧さなく算出する。

    第一候補: 年間配当額 (dividendRate / trailingAnnualDividendRate) ÷ 株価。
    フォールバック: dividendYield をパーセント値として採用 (yfinance >= 0.2.54 の
    規約。旧来の「<1 なら ×100」ヒューリスティックは実利回り 1% 未満の銘柄を
    100 倍に誤変換するため廃止)。
    """
    rate = _to_float(info.get("dividendRate") or info.get("trailingAnnualDividendRate"))
    if rate and price:
        return round(rate / price * 100, 2)
    dy = _to_float(info.get("dividendYield"))
    return round(dy, 2) if dy is not None else None


def apply_filters(rows: list[dict], capital: int, market_cap_min: float) -> list[dict]:
    """Lot + market-cap filters, then attach value_score and sort descending."""
    out = []
    for r in rows:
        if not lot_filter(r.get("price"), capital):
            continue
        mc = r.get("market_cap")
        # market_cap 不明は意図的に通す: yfinance の info 欠落で候補を取り逃すより、
        # 円卓討議の段階で時価総額を再確認するほうが安全 (一次フィルタの思想)
        if mc is not None and mc < market_cap_min:
            continue
        score = value_score(r)
        if score is None:
            continue
        out.append({**r, "value_score": score})
    out.sort(key=lambda x: x["value_score"], reverse=True)
    return out


_QUALITY_LABELS = {"roe": "ROE", "equity_ratio": "自己資本比率", "operating_cf": "営業CF"}


def quality_check(metrics: dict) -> dict:
    """標準基準の質的判定 (pure)。ROE≥8% / 自己資本比率≥30% / 営業CF>0.

    metrics: {"roe_pct": float|None, "equity_ratio_pct": float|None, "operating_cf": float|None}
    欠損 (None) は不合格にせず missing に列挙する (lenient — 円卓討議で再確認)。
    戻り値: {"passed": bool, "failed": [基準名], "missing": [基準名]}
    """
    checks = {
        "roe": None if metrics.get("roe_pct") is None else metrics["roe_pct"] >= _ROE_MIN_PCT,
        "equity_ratio": None if metrics.get("equity_ratio_pct") is None
        else metrics["equity_ratio_pct"] >= _EQUITY_RATIO_MIN_PCT,
        "operating_cf": None if metrics.get("operating_cf") is None else metrics["operating_cf"] > 0,
    }
    failed = [k for k, v in checks.items() if v is False]
    missing = [k for k, v in checks.items() if v is None]
    return {"passed": not failed, "failed": failed, "missing": missing}


def apply_quality(candidates: list[dict], top_n: int = _QUALITY_TOP_N,
                  fetch=None, pause_sec: float | None = None) -> tuple[list[dict], list[dict]]:
    """バリュースコア上位 top_n に質的足切りを適用する (二段階方式の第二段)。

    基準不合格は除外、欠損は ⚠ フラグ付きで残す。top_n 以降の候補は
    チェック対象外として捨てる (上位だけ見れば円卓討議の入力には十分)。
    戻り値: (survivors, dropped) — どちらも roe_pct 等と quality_missing/failed 付き。
    """
    import time

    fetch = fetch or _fetch_quality_metrics
    if pause_sec is None:
        pause_sec = _INFO_PAUSE_SEC
    survivors: list[dict] = []
    dropped: list[dict] = []
    for n, c in enumerate(candidates[:top_n], 1):
        q = fetch(f"{c['ticker']}.T")
        verdict = quality_check(q)
        row = {**c, **q, "quality_missing": verdict["missing"], "quality_failed": verdict["failed"]}
        (survivors if verdict["passed"] else dropped).append(row)
        if n % 20 == 0:
            logger.info("Stage 3 (質チェック): %d/%d ...", n, min(top_n, len(candidates)))
        if pause_sec and n < min(top_n, len(candidates)):
            time.sleep(pause_sec)
    return survivors, dropped


# ---------------------------------------------------------------------------
# Live data fetch
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 200          # Stage 1 バッチサイズ (1リクエストの銘柄数)
_CHUNK_PAUSE_SEC = 3.0     # Stage 1 チャンク間の待機
_INFO_PAUSE_SEC = 0.4      # Stage 2 銘柄間の待機
_RATE_LIMIT_BACKOFF_SEC = 30.0


def _download_chunk_closes(chunk: list[str]):
    """yf.download 1チャンク分の Close を返す。空 DataFrame (例外なしのレート制限
    応答) も失敗として raise し、呼び出し側のリトライに乗せる。"""
    import yfinance as yf

    px = yf.download(chunk, period="5d", interval="1d", progress=False, auto_adjust=False)
    if px is None or px.empty or "Close" not in px:
        raise RuntimeError("empty download result (rate limited?)")
    return px["Close"]


def _batch_closes(symbols: list[str]) -> dict[str, float]:
    """Chunked yf.download to avoid rate limits. Returns {symbol: latest close}."""
    import time

    out: dict[str, float] = {}
    for i in range(0, len(symbols), _CHUNK_SIZE):
        chunk = symbols[i:i + _CHUNK_SIZE]
        logger.info("Stage 1: chunk %d-%d / %d ...", i + 1, i + len(chunk), len(symbols))
        try:
            closes = _download_chunk_closes(chunk)
        except Exception as e:
            logger.warning("chunk download failed (%s) — %ds 待機して1回だけ再試行", e, int(_RATE_LIMIT_BACKOFF_SEC))
            time.sleep(_RATE_LIMIT_BACKOFF_SEC)
            try:
                closes = _download_chunk_closes(chunk)
            except Exception as e2:
                logger.error("chunk download failed twice (%s) — %d 銘柄スキップ", e2, len(chunk))
                continue
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


def _fetch_quality_metrics(sym: str) -> dict:
    """yfinance から質チェック用 3 指標を取得。取れないものは None のまま返す。

    ROE / 営業CF は .info から、 自己資本比率は .balance_sheet
    (Total Assets / Stockholders Equity) から算出する。
    """
    out: dict = {"roe_pct": None, "equity_ratio_pct": None, "operating_cf": None}
    import yfinance as yf

    try:
        t = yf.Ticker(sym)
        info = t.info or {}
    except Exception as e:
        logger.debug("quality info failed for %s: %s", sym, e)
        return out

    roe = info.get("returnOnEquity")  # 小数 (0.085 = 8.5%)
    if roe is not None:
        out["roe_pct"] = round(float(roe) * 100, 1)
    ocf = info.get("operatingCashflow")
    if ocf is not None:
        out["operating_cf"] = float(ocf)

    try:
        bs = t.balance_sheet
        if bs is not None and not bs.empty:
            col = bs.columns[0]  # 最新期
            ta = _bs_value(bs, col, ("Total Assets",))
            se = _bs_value(bs, col, ("Stockholders Equity", "Common Stock Equity",
                                     "Total Equity Gross Minority Interest"))
            if ta and se and ta > 0:
                out["equity_ratio_pct"] = round(se / ta * 100, 1)
    except Exception as e:
        logger.debug("balance_sheet failed for %s: %s", sym, e)
    return out


def _bs_value(bs, col, row_names: tuple[str, ...]) -> float | None:
    """balance_sheet DataFrame から候補行名の最初のヒットを float で返す。"""
    for name in row_names:
        if name in bs.index:
            v = bs.loc[name, col]
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f == f:  # not NaN
                return f
    return None


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
        rows.append(
            {
                "ticker": meta["ticker"],
                "name": meta["name"],
                "sector33": meta["sector33"],
                "price": price,
                "per": effective_per(info),
                "pbr": _to_float(info.get("priceToBook")),
                "dividend_yield_pct": dividend_yield_pct(info, price),
                "market_cap": _to_float(info.get("marketCap")),
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
                 coverage: dict | None = None, dropped: list[dict] | None = None) -> str:
    lines = [
        f"# 割安株スクリーニング — {today.isoformat()}",
        "",
        f"- ユニバース: 東証プライム (金融除く) {universe_size} 銘柄",
        f"- 想定総資本: ¥{capital:,} / 最低売買単位フィルタ: 100株 ≤ ¥{int(capital * _LOT_RATIO_MAX):,}"
        f" (= 株価 {int(capital * _LOT_RATIO_MAX / _LOT_SIZE):,}円以下)",
        f"- 通過: {len(candidates)} 銘柄 → Top {min(top, len(candidates))} を表示",
    ]
    quality_checked = dropped is not None
    if quality_checked:
        lines.append(
            f"- 質的足切り (スコア上位{_QUALITY_TOP_N}対象): ROE≥{_ROE_MIN_PCT:.0f}% / "
            f"自己資本比率≥{_EQUITY_RATIO_MIN_PCT:.0f}% / 営業CF>0 — 除外 {len(dropped)} 銘柄"
            " (欠損は ⚠ で残す)"
        )
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
    ]
    if quality_checked:
        lines += [
            "| # | コード | 銘柄 | 業種 | 株価 | PER | PBR | 配当利回り | スコア | ROE | 自己資本比率 | 営業CF |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    else:
        lines += [
            "| # | コード | 銘柄 | 業種 | 株価 | PER | PBR | 配当利回り | スコア |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    for i, c in enumerate(candidates[:top], 1):
        if c.get("per") is None:
            per = "—"
        elif c["per"] <= 0:
            per = "赤字"
        else:
            per = f"{c['per']:.1f}"
        pbr = f"{c['pbr']:.2f}" if c.get("pbr") is not None else "—"
        dy = f"{c['dividend_yield_pct']:.2f}%" if c.get("dividend_yield_pct") is not None else "—"
        row = (
            f"| {i} | {c['ticker']} | {c['name']} | {c['sector33']} | {c['price']:,.0f} | {per} | {pbr} | {dy} | {c['value_score']} |"
        )
        if quality_checked:
            roe = f"{c['roe_pct']:.1f}%" if c.get("roe_pct") is not None else "⚠—"
            er = f"{c['equity_ratio_pct']:.1f}%" if c.get("equity_ratio_pct") is not None else "⚠—"
            ocf = c.get("operating_cf")
            ocf_s = "✓" if (ocf is not None and ocf > 0) else "⚠—"
            row += f" {roe} | {er} | {ocf_s} |"
        lines.append(row)
    if dropped:
        lines += ["", f"### 質的足切りで除外 ({len(dropped)} 銘柄)", ""]
        for c in dropped[:20]:
            reasons = []
            for k in c.get("quality_failed", []):
                label = _QUALITY_LABELS.get(k, k)
                if k == "roe":
                    reasons.append(f"{label} {c['roe_pct']:.1f}%")
                elif k == "equity_ratio":
                    reasons.append(f"{label} {c['equity_ratio_pct']:.1f}%")
                else:
                    reasons.append(f"{label} ≤0")
            lines.append(f"- {c['ticker']} {c['name']} (スコア {c['value_score']}): {', '.join(reasons)}")
        if len(dropped) > 20:
            lines.append(f"- … 他 {len(dropped) - 20} 銘柄")
    lines.append("")
    return "\n".join(lines) + "\n"


def run(capital: int = _DEFAULT_CAPITAL, top: int = 15, today: date | None = None,
        quality: bool = True) -> Path:
    today = today or date.today()
    universe = load_universe_tickers()
    if not universe:
        raise SystemExit("ユニバース未取得。先に --refresh-universe を実行してください。")

    cfg = load_universe_config()
    mc_min = float((cfg.get("filters") or {}).get("market_cap_min_jpy") or 10_000_000_000)

    rows, coverage = fetch_metrics(universe, capital)
    candidates = apply_filters(rows, capital, mc_min)
    dropped = None
    if quality:
        logger.info("Stage 3: 質的足切り (スコア上位 %d) ...", _QUALITY_TOP_N)
        candidates, dropped = apply_quality(candidates)
    report = build_report(candidates, capital, top, today, len(universe), coverage, dropped=dropped)
    return write_report(f"screen-{today.strftime('%Y%m%d')}.md", report)


if __name__ == "__main__":
    import argparse

    from src.tools.common import utf8_stdout

    utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser(description="割安株スクリーニング (定量一次フィルタ)")
    ap.add_argument("--refresh-universe", action="store_true", help="JPX 上場一覧を再取得")
    ap.add_argument("--capital", type=int, default=_DEFAULT_CAPITAL, help="想定総資本 (円)")
    ap.add_argument("--top", type=int, default=15, help="表示件数")
    ap.add_argument("--lot-ratio", type=float, default=_LOT_RATIO_MAX,
                    help="最低売買単位/総資本 の上限 (既定0.10。攻め方針は0.15)")
    ap.add_argument("--no-quality", action="store_true", help="質的足切り (Stage 3) をスキップ")
    args = ap.parse_args()

    _LOT_RATIO_MAX = args.lot_ratio  # noqa: F811 — CLI override (lot_filter はモジュールグローバルを参照)
    globals()["_LOT_RATIO_MAX"] = args.lot_ratio

    if args.refresh_universe:
        refresh_universe()
    else:
        run(capital=args.capital, top=args.top, quality=not args.no_quality)
