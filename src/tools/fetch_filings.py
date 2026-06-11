"""CLI: download & cache disclosure documents for JP tickers.

Usage (from ai-hedge-fund/):

    poetry run python -m src.tools.fetch_filings 4751            # 有報PDF + 決算短信PDF + XBRL12項目
    poetry run python -m src.tools.fetch_filings 4751 8001       # 複数銘柄
    poetry run python -m src.tools.fetch_filings 4751 --tanshin  # 決算短信のみ (EDINETキー不要)
    poetry run python -m src.tools.fetch_filings --list          # キャッシュ済み一覧

Everything is cached in <repo>/data/filings/ (SQLite + PDF). Re-running is a
no-op when the latest documents are already on disk.
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.tools.common import utf8_stdout

utf8_stdout()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# edinet_tools は最新有報の探索や --history の周年プローブで日付を1日ずつ叩き、
# その都度 INFO を吐く。10年スキャンでは数百行に達して結果行が stdout 切り捨てで
# 埋もれるため、ライブラリ側のログは WARNING 以上に抑える (リトライ・失敗は残る)。
logging.getLogger("edinet_tools").setLevel(logging.WARNING)

_BUFFETT_ITEMS = [
    "capital_expenditure",
    "depreciation_and_amortization",
    "net_income",
    "outstanding_shares",
    "total_assets",
    "total_liabilities",
    "shareholders_equity",
    "dividends_and_other_cash_distributions",
    "issuance_or_purchase_of_equity_shares",
    "gross_profit",
    "revenue",
    "free_cash_flow",
]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Download & cache JP disclosure documents")
    p.add_argument("tickers", nargs="*", help="4-digit JP tickers (e.g. 4751 8001)")
    p.add_argument("--tanshin", action="store_true", help="決算短信 PDF のみ (TDnet, キー不要)")
    p.add_argument("--yuho", action="store_true", help="有報 PDF のみ (EDINET, 要キー)")
    p.add_argument("--items", action="store_true", help="XBRL 12項目抽出のみ (EDINET, 要キー)")
    p.add_argument("--history", type=int, nargs="?", const=10, default=None, metavar="N",
                   help="過去 N 期分 (既定10) の財務時系列を取得・表示 (EDINET, 要キー)")
    p.add_argument("--list", action="store_true", help="キャッシュ済みドキュメント一覧を表示")
    args = p.parse_args(argv)

    from src.tools import edinet, filings_store, tdnet

    if args.list:
        rows = filings_store.list_filings()
        if not rows:
            print("(キャッシュなし)")
        for r in rows:
            print(f"{r['ticker']}  {r['source']:6s} {r['doc_type']:12s} {r['fetched_at'][:10]}  {r['file_path']}")
        return 0

    if not args.tickers:
        p.error("ticker を指定してください (または --list)")

    do_all = not (args.tanshin or args.yuho or args.items or args.history)
    failures = 0

    for ticker in args.tickers:
        print(f"=== {ticker} ===")
        if args.history:
            rows = edinet.get_line_items_history(ticker, _BUFFETT_ITEMS, periods=args.history)
            if rows:
                print(edinet.build_history_report(ticker, rows))
            else:
                print("  財務時系列 : 取得失敗 (EDINET_API_KEY 未設定?)")
                failures += 1
        if do_all or args.tanshin:
            path = tdnet.download_kessan_tanshin(ticker)
            print(f"  決算短信 PDF : {path or '取得失敗'}")
            failures += path is None
        if do_all or args.yuho:
            path = edinet.fetch_yuho_pdf(ticker)
            print(f"  有報 PDF     : {path or '取得失敗 (EDINET_API_KEY 未設定?)'}")
            failures += path is None
        if do_all or args.items:
            items = edinet.get_line_items_for_ticker(ticker, _BUFFETT_ITEMS)
            if items:
                got = sum(1 for k in _BUFFETT_ITEMS if items.get(k) is not None)
                print(f"  XBRL 12項目  : {got}/12 抽出 (期末 {items.get('report_period')})")
            else:
                print("  XBRL 12項目  : 取得失敗 (EDINET_API_KEY 未設定?)")
                failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
