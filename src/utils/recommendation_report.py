"""REC-YYYYMMDD-NNN-{ticker}.md report formatter.

Produces shareholder-facing Markdown reports following the format defined in
E:\\Company\\docs\\recommendation-template.md. Each run of the hedge fund
generates one report per ticker, dropped into REC_REPORTS_DIR.

Conventions:
  - Recommendation ID: REC-YYYYMMDD-NNN (NNN = monotonic per-day counter)
  - File path: {output_dir}/REC-{YYYYMMDD}-{NNN}-{ticker}.md
  - Default output_dir: E:\\Company\\ai-hedge-fund\\data\\reports\\
  - Override via env: JP_FORK_REPORTS_DIR
  - All credentials read via os.environ.get() (none required for this module)

Action mapping (virattt -> E:\\Company recommendation actions):
  - 'buy'    -> "Buy (現物)"
  - 'sell'   -> "Sell (現物)"
  - 'short'  -> "Pass (信用空売り推奨はガバナンス上不可)"
  - 'cover'  -> "Pass (空売り解消)"
  - 'hold'   -> "Hold / Watch"
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from src.agents.cio_consensus import CONFIDENCE_FLOOR

logger = logging.getLogger(__name__)


_DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reports"


def _resolve_reports_dir(output_dir: Path | str | None = None) -> Path:
    """Resolve REC reports directory honoring JP_FORK_REPORTS_DIR env override."""
    if output_dir is not None:
        return Path(output_dir)
    env = os.environ.get("JP_FORK_REPORTS_DIR")
    if env:
        return Path(env)
    return _DEFAULT_REPORTS_DIR


_REC_FILENAME_RE = re.compile(r"^REC-(\d{8})-(\d{3})-")


def next_rec_id(output_dir: Path, on_date: datetime | None = None) -> str:
    """Compute the next monotonic REC-YYYYMMDD-NNN id for a given date.

    Scans existing files in output_dir for today's prefix and returns the
    next available 3-digit serial. Creates output_dir if missing.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    on_date = on_date or datetime.now()
    date_key = on_date.strftime("%Y%m%d")
    max_serial = 0
    for entry in output_dir.iterdir():
        if not entry.is_file():
            continue
        m = _REC_FILENAME_RE.match(entry.name)
        if m and m.group(1) == date_key:
            max_serial = max(max_serial, int(m.group(2)))
    return f"REC-{date_key}-{max_serial + 1:03d}"


_ACTION_LABEL = {
    "buy": "Buy (現物)",
    "sell": "Sell (現物)",
    "hold": "Hold / Watch",
    "short": "Pass (信用空売りはガバナンス上不可)",
    "cover": "Pass (空売り解消)",
}

_ACTION_DIRECTION = {
    "buy": "買い (現物のみ・信用なし)",
    "sell": "売り (現物のみ・信用なし)",
    "hold": "Hold / Watch",
    "short": "見送り (空売り禁止)",
    "cover": "見送り (空売り解消禁止)",
}


_CONSENSUS_TYPE_LABEL = {
    "strong": "Strong Consensus (全会一致 + 全員確信度 ≥ 50)",
    "soft": "Soft Consensus (全会一致だが一部の確信度 < 50)",
    "split": "Split (2方向に意見分裂)",
    "diverged": "Diverged (3方向に意見分裂)",
    "insufficient": "未評価 (PM 数 < 2)",
}

_DIRECTION_LABEL = {
    "bullish": "Bullish",
    "bearish": "Bearish",
    "neutral": "Neutral",
    "mixed": "Mixed",
    "unknown": "Unknown",
}


def _consensus_label(per_pm: list[dict], cio_consensus: dict | None = None) -> str:
    """Return CIO-style consensus label.

    Prefers the canonical CIO classification (Round 1.5 cio_consensus_agent)
    when present; falls back to a local count-based label otherwise so this
    function still works in unit tests that don't run the workflow.
    """
    if cio_consensus and isinstance(cio_consensus, dict):
        ctype = cio_consensus.get("type")
        if ctype:
            type_lbl = _CONSENSUS_TYPE_LABEL.get(ctype, ctype)
            direction = cio_consensus.get("direction", "unknown")
            dir_lbl = _DIRECTION_LABEL.get(direction, direction)
            bull = cio_consensus.get("bullish", 0)
            bear = cio_consensus.get("bearish", 0)
            neu = cio_consensus.get("neutral", 0)
            return f"{type_lbl} — {dir_lbl} ({bull} Bull / {neu} Neutral / {bear} Bear)"

    # Fallback (no Round 1.5 result available)
    sigs = [p["signal"] for p in per_pm]
    if not sigs:
        return "未評価"
    bullish = sigs.count("bullish")
    bearish = sigs.count("bearish")
    neutral = sigs.count("neutral")
    total = len(sigs)
    if bullish == total:
        return "Strong Consensus: 全会一致 Bullish"
    if bearish == total:
        return "Strong Consensus: 全会一致 Bearish"
    if neutral == total:
        return "Strong Consensus: 全会一致 Neutral"
    if bullish > bearish and bullish > neutral:
        return f"Majority Bullish ({bullish}/{total})"
    if bearish > bullish and bearish > neutral:
        return f"Majority Bearish ({bearish}/{total})"
    if neutral > bullish and neutral > bearish:
        return f"Majority Neutral ({neutral}/{total})"
    return f"Split ({bullish} Bull / {neutral} Neutral / {bearish} Bear)"


def _gov_warning_for_action(action: str) -> str | None:
    """Return a governance warning string when the action conflicts with rules."""
    if action in ("short", "cover"):
        return (
            "⚠️ **ガバナンス警告**: E:\\Company の運用規程 (CLAUDE.md) は"
            "**現物のみ・信用なし** を定めている。本推奨は構造上採用不可。"
            "Hold/Pass として読み替えること。"
        )
    return None


def _format_per_pm_row(agent_name: str, sig: dict) -> str:
    sig_label = (sig.get("signal") or "?").upper()
    conf = sig.get("confidence")
    conf_str = f"{conf:.0f}%" if isinstance(conf, (int, float)) else "-"
    reason = (sig.get("reasoning") or "").replace("|", "/").replace("\n", " ")
    if len(reason) > 200:
        reason = reason[:197] + "..."
    return f"| {agent_name} | {sig_label} | {conf_str} | {reason} |"


def _pm_agent_names() -> list[str]:
    """Whitelist of PM-class agents (vs. specialist analysts) for the consensus row."""
    return [
        "warren_buffett_agent",
        "charlie_munger_agent",
        "ben_graham_agent",
        "bill_ackman_agent",
        "michael_burry_agent",
        "mohnish_pabrai_agent",
        "nassim_taleb_agent",
        "peter_lynch_agent",
        "phil_fisher_agent",
        "stanley_druckenmiller_agent",
        "aswath_damodaran_agent",
        "rakesh_jhunjhunwala_agent",
        "cathie_wood_agent",
    ]


def _human_agent_name(raw: str) -> str:
    """warren_buffett_agent -> Warren Buffett."""
    name = raw.replace("_agent", "").replace("_", " ").title()
    return name


def format_report(
    ticker: str,
    rec_id: str,
    result: dict,
    *,
    model_name: str,
    start_date: str,
    end_date: str,
    valid_until: datetime | None = None,
) -> str:
    """Render one REC Markdown report for a single ticker.

    Args:
        ticker: e.g. '4751'
        rec_id: e.g. 'REC-20260520-001'
        result: dict returned by run_hedge_fund() with keys
                'decisions' (per-ticker) and 'analyst_signals' (per-agent per-ticker)
        model_name: LLM model used (for traceability)
        start_date, end_date: analysis window
        valid_until: expiry of this recommendation (default: end_date + 14d)
    """
    now = datetime.now().astimezone()
    decision = (result.get("decisions") or {}).get(ticker, {})
    signals = result.get("analyst_signals") or {}
    cio_consensus = (result.get("consensus") or {}).get(ticker)

    pm_agents = _pm_agent_names()
    per_pm_signals = []
    per_analyst_signals = []
    risk_metrics = {}

    for agent_name, ticker_map in signals.items():
        sig = (ticker_map or {}).get(ticker)
        if not sig:
            continue
        entry = {"agent": agent_name, **sig}
        if agent_name in pm_agents:
            per_pm_signals.append(entry)
        elif agent_name == "risk_management_agent":
            risk_metrics = sig
        else:
            per_analyst_signals.append(entry)

    action = (decision.get("action") or "").lower()
    qty = decision.get("quantity", 0)
    pm_confidence = decision.get("confidence", 0)
    pm_reasoning = decision.get("reasoning", "")
    gov_warning = _gov_warning_for_action(action)

    if valid_until is None:
        from datetime import timedelta
        try:
            base = datetime.strptime(end_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            base = now
        valid_until = base + timedelta(days=14)

    current_price = risk_metrics.get("current_price")
    vol = (risk_metrics.get("volatility_metrics") or {})
    daily_vol = vol.get("daily_volatility")
    annual_vol = vol.get("annualized_volatility")
    remaining_limit = risk_metrics.get("remaining_position_limit")

    lines: list[str] = []
    lines.append(f"# 推奨レポート — {ticker}")
    lines.append("")
    lines.append(f"**Recommendation ID**: {rec_id}  ")
    lines.append(f"**作成日時**: {now.strftime('%Y-%m-%d %H:%M %Z')}  ")
    lines.append(f"**有効期限**: {valid_until.strftime('%Y-%m-%d')}  ")
    lines.append(f"**分析期間**: {start_date} 〜 {end_date}  ")
    lines.append(f"**LLM モデル**: {model_name}  ")
    lines.append(f"**フェーズ**: 学習・検証 (ペーパートレード) — **実弾発注なし**")
    lines.append("")

    if gov_warning:
        lines.append("---")
        lines.append("")
        lines.append(gov_warning)
        lines.append("")

    # Section 1: Action
    lines.append("---")
    lines.append("")
    lines.append("## 1. アクション提案")
    lines.append("")
    lines.append("| 項目 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 方向 | {_ACTION_DIRECTION.get(action, '不明')} |")
    lines.append(f"| 銘柄 | {ticker} |")
    if current_price is not None:
        lines.append(f"| 現株価 | ¥{current_price:,.1f} |")
    lines.append(f"| 推奨数量 | {qty} 株 |")
    if current_price and qty:
        position_value = current_price * abs(qty)
        lines.append(f"| 想定建玉金額 | ¥{position_value:,.0f} |")
    lines.append(f"| Portfolio Manager 判断 | **{_ACTION_LABEL.get(action, action.upper())}** |")
    lines.append(f"| 確信度 | {pm_confidence:.0f}% |" if isinstance(pm_confidence, (int, float)) else "| 確信度 | - |")
    lines.append(f"| Stop / Time Stop | [株主決定] |")
    lines.append("")

    # Section 2: PM Consensus
    lines.append("## 2. PM Consensus (CIO Round 1.5)")
    lines.append("")
    lines.append(f"**合意度**: {_consensus_label(per_pm_signals, cio_consensus)}")
    if cio_consensus:
        conf_floor = cio_consensus.get("confidence_floor")
        low_conf = cio_consensus.get("low_conf_pms") or []
        if conf_floor is not None:
            lines.append(f"**最低確信度**: {conf_floor:.0f}%")
        if low_conf:
            human_low = ", ".join(_human_agent_name(a) for a in low_conf)
            lines.append(f"**低確信 PM (< {CONFIDENCE_FLOOR}%)**: {human_low}")
    lines.append("")
    if per_pm_signals:
        lines.append("| PM | 判断 | 確信度 | 根拠 |")
        lines.append("|---|---|---|---|")
        for s in per_pm_signals:
            lines.append(_format_per_pm_row(_human_agent_name(s["agent"]), s))
    else:
        lines.append("(PM 起動なし)")
    lines.append("")

    # Section 3: 専門 Analyst
    if per_analyst_signals:
        lines.append("## 3. 専門 Analyst")
        lines.append("")
        lines.append("| Analyst | 判断 | 確信度 | 根拠 |")
        lines.append("|---|---|---|---|")
        for s in per_analyst_signals:
            lines.append(_format_per_pm_row(_human_agent_name(s["agent"]), s))
        lines.append("")

    # Section 4: Risk Check
    lines.append("## 4. リスク評価 (Risk Manager)")
    lines.append("")
    if risk_metrics:
        lines.append("| 観点 | 値 |")
        lines.append("|---|---|")
        if daily_vol is not None:
            lines.append(f"| 日次ボラ | {daily_vol*100:.2f}% |")
        if annual_vol is not None:
            lines.append(f"| 年率ボラ | {annual_vol*100:.1f}% |")
        if remaining_limit is not None:
            lines.append(f"| Risk Manager 上限 (Long) | ¥{remaining_limit:,.0f} |")
        reasoning = risk_metrics.get("reasoning")
        if isinstance(reasoning, dict):
            pv = reasoning.get("portfolio_value")
            if pv:
                lines.append(f"| ポートフォリオ規模 | ¥{pv:,.0f} |")
            ra = reasoning.get("risk_adjustment")
            if ra:
                lines.append(f"| Risk 調整説明 | {ra} |")
        # CIO governance comparison (manual review prompts)
        lines.append("")
        lines.append("**CIO ガバナンス対比** (株主が手動チェック):")
        lines.append("")
        lines.append("| CIO ルール (CLAUDE.md) | 推奨値 | 株主判定 |")
        lines.append("|---|---|---|")
        lines.append("| 1トレード当たりリスク ≤ ¥5,000 | [要計算] | ☐ OK / ☐ NG |")
        lines.append("| 単一銘柄ウェイト ≤ 10% (Buffett 例外 20%) | [要計算] | ☐ OK / ☐ NG |")
        lines.append("| セクター集中 ≤ 30% | [要確認] | ☐ OK / ☐ NG |")
        lines.append("| 流動性 (5日平均売買代金 ≥ ¥1億) | [要確認] | ☐ OK / ☐ NG |")
        lines.append("| 信用空売りなし (現物のみ) | " + ("⚠️ NG" if action in ("short", "cover") else "OK") + " | - |")
    else:
        lines.append("(Risk Manager 起動なし)")
    lines.append("")

    # Section 5: Reasoning (Portfolio Manager)
    lines.append("## 5. Portfolio Manager の最終理由")
    lines.append("")
    lines.append(f"{pm_reasoning or '(なし)'}")
    lines.append("")

    # Section 6: 株主決定
    lines.append("## 6. 株主決定事項")
    lines.append("")
    lines.append("- [ ] 推奨を承認し、ペーパートレード記録に追加 (実弾は別途手動)")
    lines.append("- [ ] 一部修正で承認 (修正内容: ___ )")
    lines.append("- [ ] 見送る (理由: ___ )")
    lines.append("- [ ] Watch List に登録して継続監視")
    lines.append("")
    lines.append("**承認日**: ____  **承認者署名**: ____")
    lines.append("")

    # Section 7: 注意
    lines.append("## 7. 注意事項")
    lines.append("")
    lines.append("- これは推奨であり、**実弾発注ではない**")
    lines.append("- システムは証券口座 API と未接続。発注は株主の手動操作のみ")
    lines.append("- リテール算法トレーダーの 71-89% が損失を出す事実を踏まえ判断のこと")
    lines.append("- データ source: yfinance (Phase 2.1) + EDINET XBRL (Phase 2.3、API キー設定時のみ)")
    lines.append("")

    return "\n".join(lines)


def save_report(report_text: str, rec_id: str, ticker: str, output_dir: Path | str | None = None) -> Path:
    """Persist a single REC report as Markdown."""
    out_dir = _resolve_reports_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{rec_id}-{ticker}.md"
    path.write_text(report_text, encoding="utf-8")
    logger.info("Wrote REC report: %s", path)
    return path


def emit_reports_for_run(
    result: dict,
    *,
    tickers: list[str],
    model_name: str,
    start_date: str,
    end_date: str,
    output_dir: Path | str | None = None,
) -> list[Path]:
    """High-level entry: generate one report per ticker, return saved paths."""
    out_dir = _resolve_reports_dir(output_dir)
    written: list[Path] = []
    for ticker in tickers:
        rec_id = next_rec_id(out_dir)
        report = format_report(
            ticker=ticker,
            rec_id=rec_id,
            result=result,
            model_name=model_name,
            start_date=start_date,
            end_date=end_date,
        )
        written.append(save_report(report, rec_id, ticker, out_dir))
    return written
