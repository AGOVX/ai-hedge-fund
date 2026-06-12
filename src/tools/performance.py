"""パフォーマンス測定 — equity-curve.csv からリターン・リスク指標を計算する.

学習・検証フェーズの「答え合わせ」基盤。daily_check.ps1 が貯める equity
スナップショットを読み、以下を算出する:

  - 累積リターン / 年率換算リターン
  - Sharpe / Sortino (スナップショット間リターン、rf=0、年率化 √252)
  - 最大ドローダウン
  - TOPIX (代理 1306.T) 相対の超過リターン
  - PM 別の建玉成績 (含み損益)

データが少ないうちは指標の信頼性が低い — レポートに必ず n を併記し、
n < 20 では Sharpe 系を「参考値」と明示する。

Usage:
    python -m src.tools.performance
"""

from __future__ import annotations

import csv
import logging
import math
import os
from datetime import date
from pathlib import Path

from src.tools.common import repo_root, write_report

logger = logging.getLogger(__name__)

_MIN_BARS_FOR_RATIOS = 20  # これ未満の Sharpe/Sortino は参考値扱い
_PERIODS_PER_YEAR = 252


def equity_curve_path() -> Path:
    env = os.environ.get("EQUITY_CURVE_PATH", "")
    return Path(env) if env else repo_root() / "data" / "paper-trades" / "equity-curve.csv"


# ---------------------------------------------------------------------------
# Pure metrics
# ---------------------------------------------------------------------------

def returns_from_values(values: list[float]) -> list[float]:
    """隣接スナップショット間の単純リターン。"""
    return [(b - a) / a for a, b in zip(values, values[1:]) if a]


def cumulative_return_pct(values: list[float]) -> float | None:
    if len(values) < 2 or not values[0]:
        return None
    return (values[-1] - values[0]) / values[0] * 100


def annualized_return_pct(values: list[float], dates: list[str]) -> float | None:
    """暦日ベースの年率換算。期間1年未満の年率化は誇張になるため素直に複利換算のみ。"""
    if len(values) < 2 or not values[0]:
        return None
    days = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
    if days <= 0:
        return None
    total = values[-1] / values[0]
    if total <= 0:
        return None
    return (total ** (365.25 / days) - 1) * 100


def sharpe_ratio(returns: list[float]) -> float | None:
    """rf=0、日次前提の年率化 Sharpe。分散ゼロ・データ不足は None。"""
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if var == 0:
        return None
    return mean / math.sqrt(var) * math.sqrt(_PERIODS_PER_YEAR)


def sortino_ratio(returns: list[float]) -> float | None:
    """下方偏差のみで割る Sortino。マイナスリターンが無い場合は None (∞ を避ける)。"""
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    downside = [min(r, 0.0) ** 2 for r in returns]
    dvar = sum(downside) / (len(returns) - 1)
    if dvar == 0:
        return None
    return mean / math.sqrt(dvar) * math.sqrt(_PERIODS_PER_YEAR)


def max_drawdown_pct(values: list[float]) -> float | None:
    """ピークからの最大下落率 (負の %)。"""
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        if peak:
            worst = min(worst, (v - peak) / peak)
    return worst * 100


def excess_vs_benchmark_pct(
    equity: list[float], bench: list[float]
) -> float | None:
    """同一区間の累積リターン差 (pt)。bench に欠損があれば呼び出し側で揃えること。"""
    e = cumulative_return_pct(equity)
    b = cumulative_return_pct(bench)
    if e is None or b is None:
        return None
    return e - b


def pm_breakdown(rows: list[dict]) -> dict[str, dict]:
    """value_positions() の rows を pm_sponsor 別に集計。"""
    out: dict[str, dict] = {}
    for r in rows:
        pm = r.get("pm_sponsor") or "unknown"
        agg = out.setdefault(pm, {"positions": 0, "value_jpy": 0.0, "unrealized_pl_jpy": 0.0})
        agg["positions"] += 1
        agg["value_jpy"] += r.get("value_jpy", 0.0)
        agg["unrealized_pl_jpy"] += r.get("unrealized_pl_jpy", 0.0)
    return out


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_equity_curve() -> list[dict]:
    """equity-curve.csv を読み、date 昇順の dict リストを返す。"""
    p = equity_curve_path()
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("date")]
    out = []
    for r in rows:
        try:
            out.append(
                {
                    "date": r["date"],
                    "equity_jpy": float(r["equity_jpy"]),
                    "topix_close": float(r["topix_close"]) if r.get("topix_close") else None,
                }
            )
        except (KeyError, ValueError):
            logger.warning("equity-curve.csv の行を解釈できずスキップ: %s", r)
    return sorted(out, key=lambda r: r["date"])


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(v: float | None, suffix: str = "", digits: int = 2) -> str:
    return f"{v:+.{digits}f}{suffix}" if v is not None else "—"


def build_report(curve: list[dict], pm_stats: dict[str, dict], today: date) -> str:
    n = len(curve)
    lines = [
        f"# パフォーマンスレポート — {today.isoformat()}",
        "",
        "生成: `src/tools/performance.py` (観測のみ。推奨ではない)",
        "",
        f"スナップショット数: **{n}** "
        f"({curve[0]['date']} 〜 {curve[-1]['date']})" if n else "スナップショット数: **0**",
        "",
    ]
    if n < 2:
        lines += ["データ不足 (2点未満) — daily_check の蓄積を待つこと。", ""]
        return "\n".join(lines)

    dates = [r["date"] for r in curve]
    equity = [r["equity_jpy"] for r in curve]
    rets = returns_from_values(equity)

    # TOPIX 比較は topix_close が両端で取れている区間のみ
    paired = [(r["equity_jpy"], r["topix_close"]) for r in curve if r["topix_close"]]
    excess = (
        excess_vs_benchmark_pct([e for e, _ in paired], [t for _, t in paired])
        if len(paired) >= 2 else None
    )

    caveat = f" ⚠ n={n} < {_MIN_BARS_FOR_RATIOS}: 参考値" if n < _MIN_BARS_FOR_RATIOS else ""
    lines += [
        "## リターン・リスク指標",
        "",
        "| 指標 | 値 | 備考 |",
        "|---|---|---|",
        f"| 累積リターン | {_fmt(cumulative_return_pct(equity), '%')} | |",
        f"| 年率換算リターン | {_fmt(annualized_return_pct(equity, dates), '%')} | 期間1年未満は誇張に注意 |",
        f"| Sharpe (rf=0, 年率化) | {_fmt(sharpe_ratio(rets), digits=2)} |{caveat} |",
        f"| Sortino | {_fmt(sortino_ratio(rets), digits=2)} |{caveat} |",
        f"| 最大ドローダウン | {_fmt(max_drawdown_pct(equity), '%')} | |",
        f"| TOPIX 超過リターン | {_fmt(excess, 'pt')} | 代理 1306.T、両端データのある {len(paired)} 点で計算 |",
        "",
    ]

    lines += ["## PM 別建玉成績 (含み損益)", ""]
    if pm_stats:
        lines += ["| PM | 建玉数 | 評価額 | 含み損益 |", "|---|---|---|---|"]
        for pm, s in sorted(pm_stats.items()):
            lines.append(
                f"| {pm} | {s['positions']} | ¥{s['value_jpy']:,.0f} | ¥{s['unrealized_pl_jpy']:+,.0f} |"
            )
    else:
        lines.append("建玉なし — 現金100%。PM 別成績は建玉が入ってから。")
    lines += [
        "",
        "Pass/Watch 判断の答え合わせは `decision_review` のスコアカードを参照。",
        "",
    ]
    return "\n".join(lines)


def run(today: date | None = None) -> Path:
    from src.tools.portfolio import _fetch_latest_prices, load_portfolio, value_positions

    today = today or date.today()
    curve = load_equity_curve()

    data = load_portfolio()
    tickers = [p["ticker"] for p in data["positions"]]
    prices = _fetch_latest_prices(tickers) if tickers else {}
    valuation = value_positions(data, prices)
    pm_stats = pm_breakdown(valuation["rows"])

    report = build_report(curve, pm_stats, today)
    return write_report(f"performance-{today.strftime('%Y%m%d')}.md", report)


if __name__ == "__main__":
    from src.tools.common import utf8_stdout

    utf8_stdout()
    logging.basicConfig(level=logging.WARNING)
    run()
