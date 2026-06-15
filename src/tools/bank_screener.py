"""銀行株バリュー・スクリーナー (Layer 1: yfinance).

普通株向けの screener.py は質的足切り (自己資本比率≥30% / 営業CF>0) が銀行に効かず、
ユニバースからも銀行業を除外している。本モジュールは **有名投資家の銀行バリュー指標**
を組み合わせた専用スコアで、銀行を正しく拾う。

設計 (株主承認 2026-06-15):

  核心メトリック = 「ROEに対して PBR が安いか」(Graham の PBR × Buffett の ROE)
    適正PBR ≈ ROE ÷ 株主資本コスト(既定8%)。例: ROE12% → 適正PBR≈1.5。
    実PBR がそれより低いほど割安 (質に対する安さ = バリュートラップ回避)。

  バリュー複合スコア (0-100):
    A. 割安×質ギャップ (適正PBR/実PBR)         重み 0.45  ← 核心
    B. 絶対PBR (≤0.5満点, ≥1.2で0)             重み 0.25
    C. 配当利回り (≥4.5%満点)                   重み 0.15
    D. ROA (≥1.0%満点, ≤0.2%で0)                重み 0.15

  質的足切り (銀行用・generic フィルタを差し替え):
    ROE≥4% (構造的低収益=トラップ) / ROA≥0.2% (収益力下限)。欠損は ⚠ で残す (lenient)。

  触媒フラグ (短中期スイング用):
    - PBR<1 → 東証「PBR1倍割れ改革」対象 (自社株買い/増配圧力)
    - 全行が BOJ 利上げ→NIM拡大の受益候補

データ源:
  - 銘柄リスト: JPX 上場一覧から 33業種=銀行業 のみ抽出 → data/universe/jpx-banks.csv
  - 指標: yfinance (.info の priceToBook/returnOnEquity/returnOnAssets/配当/時価総額)

Layer 2 (将来): NIM・不良債権比率・CET1・経費率は決算短信/有報(EDINET/TDnet)が必要。
  上位候補のみ fetch_filings で深掘りする 2層目を enrich_from_filings() に追加予定。

Usage:
    python -m src.tools.bank_screener --refresh-universe   # 銀行リスト更新 (月1)
    python -m src.tools.bank_screener --capital 1991043    # スクリーニング
    python -m src.tools.bank_screener --top 20 --include-financials
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import date
from pathlib import Path

from src.tools.common import repo_root, write_report
from src.tools.screener import (
    _JPX_XLS_URL,
    _PRIME_LABEL,
    _batch_closes,
    _fetch_info_with_retry,
    dividend_yield_pct,
    effective_per,
)

logger = logging.getLogger(__name__)

_DEFAULT_CAPITAL = 1_991_043          # 実総資本 (real-holdings.yaml)
_LOT_SIZE = 100
_AGGRESSIVE_WEIGHT_CAP = 0.15         # 攻め方針の単一銘柄上限 (余裕度の目安)
_COST_OF_EQUITY = 0.08               # 株主資本コスト (適正PBR算出用)

# 銀行系セクター。既定は銀行業のみ。--include-financials で金融全体に広げる。
_BANK_SECTORS = {"銀行業"}
_FINANCIAL_SECTORS = {"銀行業", "証券、商品先物取引業", "保険業", "その他金融業"}

# 質的足切り (銀行用)
_ROE_MIN_PCT = 4.0
_ROA_MIN_PCT = 0.2

# スコア重み
_W_GAP, _W_PBR, _W_DIV, _W_ROA = 0.45, 0.25, 0.15, 0.15


def bank_universe_csv_path() -> Path:
    env = os.environ.get("BANK_UNIVERSE_CSV_PATH", "")
    return Path(env) if env else repo_root() / "data" / "universe" / "jpx-banks.csv"


# ---------------------------------------------------------------------------
# Universe refresh — 銀行業のみ抽出 (普通株 screener の逆)
# ---------------------------------------------------------------------------

def refresh_bank_universe(include_financials: bool = False) -> Path:
    """JPX 上場一覧をダウンロードし、銀行 (or 金融全体) の普通株を CSV にキャッシュ."""
    import io

    import httpx
    import pandas as pd

    sectors = _FINANCIAL_SECTORS if include_financials else _BANK_SECTORS
    logger.info("Downloading JPX listing master (銀行抽出: %s) ...", sectors)
    resp = httpx.get(_JPX_XLS_URL, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()

    df = pd.read_excel(io.BytesIO(resp.content))
    df.columns = [str(c).strip() for c in df.columns]

    # 市場区分は問わない (地銀はスタンダード/プライム混在)。33業種で銀行を抽出。
    banks = df[df["33業種区分"].astype(str).isin(sectors)]

    out = bank_universe_csv_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "sector33", "market"])
        for _, row in banks.iterrows():
            code = str(row["コード"]).strip()
            if len(code) == 4 and code.isdigit():
                w.writerow([
                    code,
                    str(row["銘柄名"]).strip(),
                    str(row["33業種区分"]).strip(),
                    str(row.get("市場・商品区分", "")).strip(),
                ])

    n = sum(1 for _ in out.open(encoding="utf-8")) - 1
    logger.info("Bank universe cached: %s (%d 行)", out, n)
    return out


def load_bank_universe() -> list[dict]:
    p = bank_universe_csv_path()
    if not p.exists():
        logger.warning("Bank universe not found: %s — --refresh-universe を先に実行", p)
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Scoring (pure functions)
# ---------------------------------------------------------------------------

def justified_pbr(roe_pct: float | None, coe: float = _COST_OF_EQUITY) -> float | None:
    """適正PBR ≈ ROE ÷ 株主資本コスト (Gordon 近似)。ROE12%/COE8% → 1.5。"""
    if roe_pct is None or roe_pct <= 0:
        return None
    return round((roe_pct / 100.0) / coe, 2)


def _lin(x: float, lo: float, hi: float) -> float:
    """lo→0点, hi→100点 の線形クランプ (lo>hi でも反転対応)。"""
    if hi == lo:
        return 0.0
    s = (x - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, s))


def bank_value_score(m: dict, coe: float = _COST_OF_EQUITY) -> dict | None:
    """銀行バリュー複合スコア。戻り値に内訳とギャップ倍率も含める。

    取れる指標だけで重み付き平均 (欠損に頑健)。1つも無ければ None。
    """
    pbr = m.get("pbr")
    roe = m.get("roe_pct")
    dy = m.get("dividend_yield_pct")
    roa = m.get("roa_pct")

    parts: list[tuple[float, float]] = []
    gap = None
    jpbr = justified_pbr(roe, coe)

    # A. 割安×質ギャップ (適正PBR / 実PBR)。>1 で割安。
    if jpbr is not None and pbr is not None and pbr > 0:
        gap = round(jpbr / pbr, 2)
        parts.append((_lin(gap, 1.0, 2.0), _W_GAP))   # gap1.0→0, gap≥2.0→100
    # B. 絶対PBR
    if pbr is not None and pbr > 0:
        parts.append((_lin(pbr, 1.2, 0.5), _W_PBR))   # PBR1.2→0, ≤0.5→100
    # C. 配当
    if dy is not None and dy >= 0:
        parts.append((_lin(dy, 0.0, 4.5), _W_DIV))
    # D. ROA
    if roa is not None:
        parts.append((_lin(roa, 0.2, 1.0), _W_ROA))

    if not parts:
        return None
    tw = sum(w for _, w in parts)
    score = round(sum(s * w for s, w in parts) / tw, 1)
    return {"score": score, "justified_pbr": jpbr, "gap": gap}


def bank_quality_check(m: dict) -> dict:
    """銀行用の質的足切り。ROE≥4% / ROA≥0.2%。欠損は missing (除外しない)。"""
    roe, roa = m.get("roe_pct"), m.get("roa_pct")
    checks = {
        "roe": None if roe is None else roe >= _ROE_MIN_PCT,
        "roa": None if roa is None else roa >= _ROA_MIN_PCT,
    }
    return {
        "passed": not [k for k, v in checks.items() if v is False],
        "failed": [k for k, v in checks.items() if v is False],
        "missing": [k for k, v in checks.items() if v is None],
    }


def catalyst_flags(m: dict) -> list[str]:
    """短中期スイング用の触媒フラグ。"""
    flags = []
    pbr = m.get("pbr")
    if pbr is not None and 0 < pbr < 1.0:
        flags.append("PBR<1改革")          # 東証 PBR1倍割れ改革の対象
    dy = m.get("dividend_yield_pct")
    if dy is not None and dy >= 4.0:
        flags.append("高配当")
    flags.append("BOJ利上げ受益")          # 全行共通: 利上げ→NIM拡大
    return flags


def enrich_from_filings(ticker: str) -> dict:
    """Layer 2 (将来実装): NIM・不良債権比率・CET1・経費率を短信/有報から取得.

    現状はプレースホルダ (全 None)。上位候補だけ fetch_filings(--quarter) と
    XBRL から銀行固有指標を抜く処理をここに足す。
    """
    return {"nim_pct": None, "npl_ratio_pct": None, "cet1_pct": None, "efficiency_ratio_pct": None}


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

_INFO_PAUSE_SEC = 0.4


def fetch_bank_metrics(tickers: list[dict]) -> tuple[list[dict], dict]:
    """銀行ユニバースの株価 + .info 指標を取得 (銀行は小ユニバースなので全件)."""
    import time

    symbols = [f"{t['ticker']}.T" for t in tickers]
    by_symbol = {f"{t['ticker']}.T": t for t in tickers}

    closes = _batch_closes(symbols)
    logger.info("Stage 2: %d 行の .info 取得 ...", len(closes))
    rows, info_ok = [], 0
    for n, (sym, price) in enumerate(closes.items(), 1):
        meta = by_symbol[sym]
        info = _fetch_info_with_retry(sym)
        roe = info.get("returnOnEquity")
        roa = info.get("returnOnAssets")
        if info.get("priceToBook") is not None or roe is not None:
            info_ok += 1
        rows.append({
            "ticker": meta["ticker"],
            "name": meta["name"],
            "sector33": meta["sector33"],
            "price": price,
            "per": effective_per(info),
            "pbr": info.get("priceToBook"),
            "roe_pct": round(float(roe) * 100, 1) if roe is not None else None,
            "roa_pct": round(float(roa) * 100, 2) if roa is not None else None,
            "dividend_yield_pct": dividend_yield_pct(info, price),
            "market_cap": info.get("marketCap"),
        })
        if n % 20 == 0:
            logger.info("Stage 2: %d/%d ...", n, len(closes))
        time.sleep(_INFO_PAUSE_SEC)

    coverage = {"universe": len(symbols), "price_ok": len(closes), "info_ok": info_ok}
    return rows, coverage


def screen_banks(rows: list[dict], capital: int, coe: float = _COST_OF_EQUITY,
                 quality: bool = True) -> list[dict]:
    """スコア付与 + 質的足切り + 余裕度注釈。スコア降順で返す。"""
    out = []
    for r in rows:
        sv = bank_value_score(r, coe)
        if sv is None:
            continue
        q = bank_quality_check(r)
        if quality and not q["passed"]:
            continue
        price = r.get("price") or 0
        lot_cost = price * _LOT_SIZE
        out.append({
            **r,
            "value_score": sv["score"],
            "justified_pbr": sv["justified_pbr"],
            "gap": sv["gap"],
            "lot_cost": lot_cost,
            "weight_pct": round(lot_cost / capital * 100, 1) if capital else None,
            "affordable": lot_cost <= capital * _AGGRESSIVE_WEIGHT_CAP,
            "quality_missing": q["missing"],
            "catalysts": catalyst_flags(r),
        })
    out.sort(key=lambda x: x["value_score"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(cands: list[dict], capital: int, top: int, today: date,
                 universe_size: int, coverage: dict | None = None) -> str:
    cap10 = int(capital * _AGGRESSIVE_WEIGHT_CAP)
    lines = [
        f"# 銀行株バリュー・スクリーニング — {today.isoformat()}",
        "",
        f"- ユニバース: 銀行 {universe_size} 行",
        f"- 基準資本: ¥{capital:,} / 単一上限(攻め) 15% = ¥{cap10:,} (= 株価 {cap10 // _LOT_SIZE:,}円/100株 以下が無理なく組める)",
        f"- スコア = 適正PBR/実PBR(0.45) + 絶対PBR(0.25) + 配当(0.15) + ROA(0.15)。質足切り: ROE≥{_ROE_MIN_PCT:.0f}% / ROA≥{_ROA_MIN_PCT:.1f}%",
        f"- 適正PBR = ROE ÷ 株主資本コスト{_COST_OF_EQUITY*100:.0f}%。**gap(適正/実)>1 ほど割安**",
        f"- 通過 {len(cands)} 行 → Top {min(top, len(cands))}",
    ]
    if coverage:
        lines.append(
            f"- カバレッジ: 株価 {coverage['price_ok']}/{coverage['universe']} / 指標 {coverage['info_ok']}"
        )
        if coverage["price_ok"] < coverage["universe"] * 0.8:
            lines.append("- ⚠️ 取得失敗多 (レート制限の可能性)。時間を置いて再実行を。")
    lines += [
        "",
        "**定量一次フィルタであり推奨ではない。** 採否は円卓討議(CIO)→株主承認。Layer1(yfinance)のみ。NIM/不良債権/CET1はLayer2(短信)で深掘り要。",
        "",
        "| # | コード | 銀行 | 株価 | PBR | 適正PBR | gap | ROE | ROA | 配当 | スコア | 単元¥ | ポート比 | 触媒 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(cands[:top], 1):
        pbr = f"{c['pbr']:.2f}" if c.get("pbr") is not None else "—"
        jpbr = f"{c['justified_pbr']:.2f}" if c.get("justified_pbr") is not None else "—"
        gap = f"{c['gap']:.2f}" if c.get("gap") is not None else "—"
        roe = f"{c['roe_pct']:.1f}%" if c.get("roe_pct") is not None else "⚠—"
        roa = f"{c['roa_pct']:.2f}%" if c.get("roa_pct") is not None else "⚠—"
        dy = f"{c['dividend_yield_pct']:.2f}%" if c.get("dividend_yield_pct") is not None else "—"
        wt = f"{c['weight_pct']:.1f}%" if c.get("weight_pct") is not None else "—"
        aff = "" if c.get("affordable") else "⚠"
        cats = " ".join(c.get("catalysts", []))
        lines.append(
            f"| {i} | {c['ticker']} | {c['name']} | {c['price']:,.0f} | {pbr} | {jpbr} | "
            f"{gap} | {roe} | {roa} | {dy} | {c['value_score']} | ¥{c['lot_cost']:,.0f} | {aff}{wt} | {cats} |"
        )
    lines += [
        "",
        "- gap>1.5 かつ PBR<1 が「ROE対比で割安 × 改革触媒あり」の本命ゾーン。",
        "- ⚠ポート比 = 100株が総資本の15%超 (1単元が大きすぎ。サイズ注意)。",
        "",
    ]
    return "\n".join(lines) + "\n"


def run(capital: int = _DEFAULT_CAPITAL, top: int = 20, today: date | None = None,
        quality: bool = True, coe: float = _COST_OF_EQUITY) -> Path:
    today = today or date.today()
    universe = load_bank_universe()
    if not universe:
        raise SystemExit("銀行ユニバース未取得。先に --refresh-universe を実行してください。")
    rows, coverage = fetch_bank_metrics(universe)
    cands = screen_banks(rows, capital, coe, quality)
    report = build_report(cands, capital, top, today, len(universe), coverage)
    return write_report(f"bank-screen-{today.strftime('%Y%m%d')}.md", report)


if __name__ == "__main__":
    import argparse

    from src.tools.common import utf8_stdout

    utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser(description="銀行株バリュー・スクリーナー (Layer1)")
    ap.add_argument("--refresh-universe", action="store_true", help="JPX から銀行リスト再取得")
    ap.add_argument("--include-financials", action="store_true", help="銀行業に加え証券/保険/その他金融も含める")
    ap.add_argument("--capital", type=int, default=_DEFAULT_CAPITAL, help="基準資本 (円)")
    ap.add_argument("--coe", type=float, default=_COST_OF_EQUITY, help="株主資本コスト (既定0.08)")
    ap.add_argument("--top", type=int, default=20, help="表示件数")
    ap.add_argument("--no-quality", action="store_true", help="質的足切りをスキップ")
    args = ap.parse_args()

    if args.refresh_universe:
        refresh_bank_universe(include_financials=args.include_financials)
    else:
        run(capital=args.capital, top=args.top, quality=not args.no_quality, coe=args.coe)
