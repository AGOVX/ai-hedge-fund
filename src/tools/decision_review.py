"""判断の事後評価 — 「あの Pass / Watch は正解だったか」のスコアカード.

watchlist.yaml の review_history (判断日 + outcome) を読み、判断日以降の
株価リターンを TOPIX (代理: 1306.T) と比較して採点する。

採点基準 (Pass / Watch = 「買わなかった」判断):
  - 銘柄リターン ≤ ベンチマーク       → ✅ 正解 (アンダーパフォーマーを回避)
  - 銘柄がベンチを +10pt 以内で超過    → 🟡 概ね妥当 (機会費用は軽微)
  - 銘柄がベンチを +10pt 超で超過      → ❌ 機会損失 (見送りのコストが顕在化)

これはエージェントの学習材料であり、判断の蒸し返しではない。各 PM は
次回討議でこのスコアカードを参照し、自分の見送り理由が妥当だったかを検証する。

Usage:
    python -m src.tools.decision_review
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_OPPORTUNITY_COST_THRESHOLD_PT = 10.0


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Scoring (pure)
# ---------------------------------------------------------------------------

def evaluate_decision(outcome: str, stock_ret_pct: float, bench_ret_pct: float) -> dict:
    """Score a not-Buy decision by relative return since the decision date."""
    excess = stock_ret_pct - bench_ret_pct
    if outcome in ("pass", "watch"):
        if excess <= 0:
            verdict, icon = "正解 (回避成功)", "✅"
        elif excess <= _OPPORTUNITY_COST_THRESHOLD_PT:
            verdict, icon = "概ね妥当 (機会費用軽微)", "🟡"
        else:
            verdict, icon = "機会損失 (見送りコスト顕在化)", "❌"
    else:
        verdict, icon = f"未対応の outcome: {outcome}", "❓"
    return {
        "outcome": outcome,
        "stock_ret_pct": round(stock_ret_pct, 2),
        "bench_ret_pct": round(bench_ret_pct, 2),
        "excess_pt": round(excess, 2),
        "verdict": verdict,
        "icon": icon,
    }


def period_return_pct(closes: list[tuple[str, float]], start: date) -> float | None:
    """Return % from the first close ON/AFTER start to the latest close.

    closes: chronological [(YYYY-MM-DD, close), ...].
    """
    base = None
    for d, c in closes:
        if date.fromisoformat(d[:10]) >= start:
            base = c
            break
    if base is None or not closes:
        return None
    last = closes[-1][1]
    return (last - base) / base * 100


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------

def collect_decisions(entries: list[dict]) -> list[dict]:
    """Flatten review_history across watchlist entries."""
    out = []
    for e in entries:
        for h in e.get("review_history", []) or []:
            d = h.get("date")
            out.append(
                {
                    "ticker": str(e.get("ticker", "?")),
                    "name": e.get("name", ""),
                    "decision_date": str(d)[:10] if d else None,
                    "outcome": h.get("outcome", "?"),
                    "recommendation_id": h.get("recommendation_id", ""),
                }
            )
    return [d for d in out if d["decision_date"]]


def build_scorecard(decisions: list[dict], results: list[dict | None], today: date) -> str:
    lines = [
        f"# 判断の事後評価スコアカード — {today.isoformat()}",
        "",
        "ベンチマーク: TOPIX (代理 1306.T)。Pass/Watch = 「買わなかった」判断の相対評価。",
        "",
        "| 判定日 | 銘柄 | 判断 | 銘柄リターン | TOPIX | 超過 | 評価 |",
        "|---|---|---|---|---|---|---|",
    ]
    summary = {"✅": 0, "🟡": 0, "❌": 0, "❓": 0}
    for dec, res in zip(decisions, results):
        if res is None:
            lines.append(
                f"| {dec['decision_date']} | {dec['ticker']} {dec['name']} | {dec['outcome']} | — | — | — | データ不足 |"
            )
            continue
        summary[res["icon"]] = summary.get(res["icon"], 0) + 1
        lines.append(
            f"| {dec['decision_date']} | {dec['ticker']} {dec['name']} | {dec['outcome']} "
            f"| {res['stock_ret_pct']:+.1f}% | {res['bench_ret_pct']:+.1f}% | {res['excess_pt']:+.1f}pt "
            f"| {res['icon']} {res['verdict']} |"
        )
    lines += [
        "",
        f"集計: ✅ {summary['✅']} / 🟡 {summary['🟡']} / ❌ {summary['❌']}",
        "",
        "❌ がある場合: 該当 PM は次回討議の冒頭で「なぜ見送り理由が外れたか」を1段落で自己検証すること。",
        "",
    ]
    return "\n".join(lines)


def run(today: date | None = None) -> Path:
    from src.tools.jp_data import get_prices_jp
    from src.tools.watchlist_check import load_watchlist

    today = today or date.today()
    decisions = collect_decisions(load_watchlist())
    if not decisions:
        raise SystemExit("review_history が空 — 評価対象なし")

    earliest = min(date.fromisoformat(d["decision_date"]) for d in decisions)
    start = (earliest - timedelta(days=7)).isoformat()
    end = (today + timedelta(days=1)).isoformat()

    # Benchmark series (TOPIX proxy ETF 1306)
    bench_prices = [(p.time[:10], p.close) for p in get_prices_jp("1306", start, end)]

    results: list[dict | None] = []
    series_cache: dict[str, list[tuple[str, float]]] = {}
    for dec in decisions:
        t = dec["ticker"]
        if t not in series_cache:
            series_cache[t] = [(p.time[:10], p.close) for p in get_prices_jp(t, start, end)]
        d0 = date.fromisoformat(dec["decision_date"])
        sr = period_return_pct(series_cache[t], d0)
        br = period_return_pct(bench_prices, d0)
        results.append(evaluate_decision(dec["outcome"], sr, br) if sr is not None and br is not None else None)

    card = build_scorecard(decisions, results, today)
    out_dir = repo_root() / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"decision-scorecard-{today.strftime('%Y%m%d')}.md"
    out_path.write_text(card, encoding="utf-8")
    print(card)
    print(f"[saved] {out_path}")
    return out_path


if __name__ == "__main__":
    import sys

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp932 対策
    logging.basicConfig(level=logging.WARNING)
    run()
