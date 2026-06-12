"""仮想ポートフォリオ (ペーパートレード) 管理.

data/paper-trades/current.yaml の建玉を管理し、時価評価・集中度・相関を計算する。

原則 (CLAUDE.md):
  - 推奨止まり — 実弾発注はしない。これは「推奨を採用したらどうなったか」の記録
  - 建玉追加は株主承認済みの推奨のみ (approved_by_shareholder: true が必須)

positions スキーマ:
  - ticker: "8001"
    name: "伊藤忠商事"
    sector: "卸売業"                     # 33業種区分 (セクター集中度計算に使用)
    shares: 100
    entry_price: 2050.0
    entry_date: 2026-06-15
    source_recommendation_id: REC-20260615-001
    approved_by_shareholder: true        # false のエントリは読込時に除外 (削除はしない)
    pm_sponsor: druckenmiller            # 主導した PM
    thesis: "..."                        # 1行サマリ

トップレベル任意キー:
  realized_pl_cum_jpy: 0                 # 実現損益累計 (建玉クローズ時に手動更新)

Usage:
    python -m src.tools.portfolio status     # 時価評価 + 集中度 + 相関
    python -m src.tools.portfolio snapshot   # equity-curve.csv に1行追記
    python -m src.tools.portfolio add --ticker 8001 --shares 100 --price 2050 \
        --rec-id REC-XXX --pm druckenmiller --approved-by-shareholder
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import yaml

from src.tools.common import repo_root

logger = logging.getLogger(__name__)


def portfolio_path() -> Path:
    env = os.environ.get("PORTFOLIO_PATH", "")
    return Path(env) if env else repo_root() / "data" / "paper-trades" / "current.yaml"


def equity_curve_path() -> Path:
    env = os.environ.get("EQUITY_CURVE_PATH", "")
    return Path(env) if env else repo_root() / "data" / "paper-trades" / "equity-curve.csv"


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_portfolio() -> dict:
    p = portfolio_path()
    if not p.exists():
        return {"as_of": str(date.today()), "capital_assumption_jpy": 500_000, "positions": []}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data.setdefault("positions", [])
    data.setdefault("capital_assumption_jpy", 500_000)

    # ガード: 株主承認のない建玉は計算から除外し警告。ただし _unapproved_positions に
    # 保持し、save_portfolio で書き戻す — 「読込時に弾く」のであって「保存時に消す」
    # のではない (無言のデータ削除を防ぐ)
    ok, rejected = [], []
    for pos in data["positions"]:
        (ok if pos.get("approved_by_shareholder") is True else rejected).append(pos)
    if rejected:
        logger.error(
            "approved_by_shareholder が true でない建玉を %d 件無視: %s",
            len(rejected), [p.get("ticker") for p in rejected],
        )
    data["positions"] = ok
    data["_unapproved_positions"] = rejected
    return data


def save_portfolio(data: dict) -> None:
    data["as_of"] = str(date.today())
    # 内部キー (_*) は書き出さず、load 時に除外した未承認エントリは positions に戻す
    out = {k: v for k, v in data.items() if not str(k).startswith("_")}
    out["positions"] = list(data.get("positions", [])) + list(data.get("_unapproved_positions", []))
    p = portfolio_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# 現在のペーパートレード建玉一覧\n"
        "# 管理: src/tools/portfolio.py (スキーマも同ファイル docstring 参照)\n"
        "# 株主承認済の推奨しか positions に追加してはならない\n\n"
        + yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def add_position(
    ticker: str,
    name: str,
    shares: int,
    entry_price: float,
    rec_id: str,
    pm_sponsor: str,
    approved_by_shareholder: bool,
    thesis: str = "",
    entry_date: str | None = None,
    sector: str = "",
) -> dict:
    """Add a position. Raises ValueError without explicit shareholder approval."""
    if approved_by_shareholder is not True:
        raise ValueError(
            "株主承認のない建玉は追加できない (--approved-by-shareholder を明示すること)。"
            " CLAUDE.md 原則5: 戦略の本番採用は株主承認必須。"
        )
    if shares <= 0 or entry_price <= 0:
        raise ValueError("shares / entry_price は正の値が必要")

    data = load_portfolio()
    existing = data["positions"] + data.get("_unapproved_positions", [])
    if any(p.get("ticker") == ticker for p in existing):
        raise ValueError(f"{ticker} は既に建玉に存在する (増し玉は手動で YAML 編集)")

    pos = {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "shares": shares,
        "entry_price": entry_price,
        "entry_date": entry_date or str(date.today()),
        "source_recommendation_id": rec_id,
        "approved_by_shareholder": True,
        "pm_sponsor": pm_sponsor,
        "thesis": thesis,
    }
    data["positions"].append(pos)
    save_portfolio(data)
    logger.info("建玉追加: %s %s × %d @ %.1f", ticker, name, shares, entry_price)
    return pos


# ---------------------------------------------------------------------------
# Valuation (pure functions — price map is injected)
# ---------------------------------------------------------------------------

def value_positions(data: dict, prices: dict[str, float]) -> dict:
    """Mark-to-market. prices: {ticker: latest_close}. Missing price → entry price (warning)."""
    rows = []
    total_cost = 0.0
    total_value = 0.0
    for pos in data["positions"]:
        t = pos["ticker"]
        px = prices.get(t)
        if px is None:
            logger.warning("%s の現値なし — 取得価格で代用", t)
            px = pos["entry_price"]
        cost = pos["shares"] * pos["entry_price"]
        val = pos["shares"] * px
        rows.append(
            {
                **pos,
                "current_price": px,
                "cost_jpy": round(cost),
                "value_jpy": round(val),
                "unrealized_pl_jpy": round(val - cost),
                "unrealized_pl_pct": round((val - cost) / cost * 100, 2) if cost else 0.0,
            }
        )
        total_cost += cost
        total_value += val

    capital = float(data.get("capital_assumption_jpy", 500_000))
    cash = capital - total_cost
    equity = cash + total_value
    return {
        "rows": rows,
        "cash_jpy": round(cash),
        "positions_value_jpy": round(total_value),
        "equity_jpy": round(equity),
        "unrealized_pl_jpy": round(total_value - total_cost),
        "invested_ratio_pct": round(total_cost / capital * 100, 1) if capital else 0.0,
    }


def sector_concentration(rows: list[dict]) -> dict[str, float]:
    """セクター別ウェイト (% of positions value)。sector 未記載は 'unknown'."""
    total = sum(r["value_jpy"] for r in rows) or 1
    out: dict[str, float] = {}
    for r in rows:
        s = r.get("sector") or "unknown"  # 未記載・空文字とも unknown
        out[s] = out.get(s, 0.0) + r["value_jpy"] / total * 100
    return {k: round(v, 1) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def aligned_returns(series_by_ticker: dict[str, list[tuple[str, float]]]) -> dict[str, list[float]]:
    """日付で揃えた日次リターン系列を作る (相関計算の前処理)。

    series_by_ticker: {ticker: [(YYYY-MM-DD, close), ...]}。銘柄ごとに欠損日が
    異なると単純な末尾揃えではリターンの日付がズレるため、共通日付の積集合上で
    リターンを計算する。
    """
    if not series_by_ticker:
        return {}
    date_sets = [set(d for d, _ in s) for s in series_by_ticker.values()]
    common = sorted(set.intersection(*date_sets)) if date_sets else []
    out: dict[str, list[float]] = {}
    for t, s in series_by_ticker.items():
        by_date = dict(s)
        px = [by_date[d] for d in common]
        out[t] = [(b - a) / a if a else 0.0 for a, b in zip(px, px[1:])]
    return out


def correlation_matrix(returns_by_ticker: dict[str, list[float]]) -> dict[tuple[str, str], float]:
    """ペア相関 (Pearson)。2銘柄未満や長さ不一致は空 dict。Dalio の ρ>0.70 同時保有禁止チェック用."""
    tickers = sorted(returns_by_ticker.keys())
    if len(tickers) < 2:
        return {}
    out = {}
    for i, a in enumerate(tickers):
        for b in tickers[i + 1:]:
            ra, rb = returns_by_ticker[a], returns_by_ticker[b]
            n = min(len(ra), len(rb))
            if n < 20:
                continue
            ra, rb = ra[-n:], rb[-n:]
            ma, mb = sum(ra) / n, sum(rb) / n
            cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / n
            va = sum((x - ma) ** 2 for x in ra) / n
            vb = sum((y - mb) ** 2 for y in rb) / n
            if va <= 0 or vb <= 0:
                continue
            out[(a, b)] = round(cov / (va ** 0.5 * vb ** 0.5), 3)
    return out


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def append_equity_snapshot(
    valuation: dict,
    topix_close: float | None,
    note: str = "",
    realized_pl_cum_jpy: float = 0.0,
) -> None:
    """equity-curve.csv に1行追記。同日の既存行は上書き (定期実行 + 手動実行の
    重複防止)。realized_pl_cum_jpy は current.yaml のトップレベルキー
    (建玉クローズ時に手動更新) から渡す。"""
    p = equity_curve_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ["date", "equity_jpy", "cash_jpy", "positions_value_jpy",
              "realized_pl_cum_jpy", "unrealized_pl_jpy", "topix_close", "note"]
    today = str(date.today())
    rows: list[list[str]] = []
    if p.exists():
        # utf-8-sig: 手動編集等で BOM が付いてもヘッダー判定を誤らない
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f) if r]
        if rows and rows[0][0] == "date":
            rows = rows[1:]
        rows = [r for r in rows if r[0] != today]  # 同日行は最新で置換
    rows.append([
        today, valuation["equity_jpy"], valuation["cash_jpy"],
        valuation["positions_value_jpy"], realized_pl_cum_jpy, valuation["unrealized_pl_jpy"],
        round(topix_close, 2) if topix_close is not None else "", note,
    ])
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Live wiring
# ---------------------------------------------------------------------------

def _fetch_latest_prices(tickers: list[str]) -> dict[str, float]:
    from src.tools.jp_data import get_prices_jp

    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=10)
    out = {}
    for t in tickers:
        prices = get_prices_jp(t, start.isoformat(), end.isoformat())
        if prices:
            out[t] = prices[-1].close
    return out


def _fetch_topix_proxy() -> float | None:
    """TOPIX 終値 (yfinance に ^TPX が無いため 1306.T ETF 代理を先に試す)."""
    import yfinance as yf

    for sym in ("1306.T", "^TPX"):
        try:
            h = yf.Ticker(sym).history(period="5d")
            if h is not None and not h.empty:
                return float(h["Close"].iloc[-1])
        except Exception:
            continue
    return None


def print_status() -> None:
    data = load_portfolio()
    if not data["positions"]:
        print(f"建玉なし (想定資本 ¥{data['capital_assumption_jpy']:,})。")
        print("Watch List の再エントリー条件達成 → 円卓討議 → 株主承認 を経て追加される。")
        return

    tickers = [p["ticker"] for p in data["positions"]]
    prices = _fetch_latest_prices(tickers)
    v = value_positions(data, prices)

    print(f"想定資本 ¥{data['capital_assumption_jpy']:,} / 投下率 {v['invested_ratio_pct']}% / "
          f"評価額 ¥{v['equity_jpy']:,} (含み損益 ¥{v['unrealized_pl_jpy']:+,})")
    print()
    for r in v["rows"]:
        print(f"  {r['ticker']} {r['name']}: {r['shares']}株 @ {r['entry_price']:,.0f} → "
              f"{r['current_price']:,.0f} ({r['unrealized_pl_pct']:+.1f}%) [{r['pm_sponsor']}]")

    conc = sector_concentration(v["rows"])
    if conc:
        print("\nセクター集中度:", ", ".join(f"{k} {x}%" for k, x in conc.items()))

    if len(tickers) >= 2:
        from src.tools.jp_data import get_prices_jp

        end = date.today()
        start = end - timedelta(days=400)
        series = {
            t: [(p.time[:10], p.close) for p in get_prices_jp(t, start.isoformat(), end.isoformat())]
            for t in tickers
        }
        corr = correlation_matrix(aligned_returns(series))
        if corr:
            print("\nペア相関 (Dalio ルール: ρ>0.70 は同時保有禁止):")
            for (a, b), rho in corr.items():
                flag = " ⚠️ ρ>0.70" if rho > 0.70 else ""
                print(f"  {a}×{b}: {rho}{flag}")


if __name__ == "__main__":
    import argparse

    from src.tools.common import utf8_stdout

    utf8_stdout()
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="仮想ポートフォリオ管理")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="時価評価 + 集中度 + 相関")
    sub.add_parser("snapshot", help="equity-curve.csv に追記")

    add = sub.add_parser("add", help="建玉追加 (株主承認必須)")
    add.add_argument("--ticker", required=True)
    add.add_argument("--name", default="")
    add.add_argument("--sector", default="", help="33業種区分 (セクター集中度計算用)")
    add.add_argument("--shares", type=int, required=True)
    add.add_argument("--price", type=float, required=True)
    add.add_argument("--rec-id", required=True, help="推奨レポートID (REC-...)")
    add.add_argument("--pm", required=True, help="主導PM (buffett/dalio/druckenmiller)")
    add.add_argument("--thesis", default="")
    add.add_argument("--approved-by-shareholder", action="store_true",
                     help="株主が当該推奨を承認済みであることの明示宣言")

    args = ap.parse_args()
    if args.cmd == "status":
        print_status()
    elif args.cmd == "snapshot":
        data = load_portfolio()
        prices = _fetch_latest_prices([p["ticker"] for p in data["positions"]])
        v = value_positions(data, prices)
        append_equity_snapshot(
            v, _fetch_topix_proxy(), note="manual snapshot",
            realized_pl_cum_jpy=float(data.get("realized_pl_cum_jpy", 0) or 0),
        )
        print(f"snapshot 追記: equity ¥{v['equity_jpy']:,}")
    elif args.cmd == "add":
        add_position(
            ticker=args.ticker, name=args.name, shares=args.shares, entry_price=args.price,
            rec_id=args.rec_id, pm_sponsor=args.pm,
            approved_by_shareholder=args.approved_by_shareholder, thesis=args.thesis,
            sector=args.sector,
        )
        print("建玉を追加した。`status` で確認可能。")
