"""Shared helpers for src/tools — path resolution, console encoding, report output.

repo_root / utf8_stdout / write_report は従来 4-5 モジュールに同一実装が
コピペされていたものを集約した。
"""

from __future__ import annotations

import sys
from pathlib import Path


def repo_root() -> Path:
    """E:\\Company (= src/tools/*.py の3階層上)。"""
    return Path(__file__).resolve().parents[3]


def utf8_stdout() -> None:
    """Windows cp932 コンソールでの UnicodeEncodeError 対策 (CLI 冒頭で呼ぶ)。"""
    enc = getattr(sys.stdout, "encoding", None)
    if enc and enc.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def write_report(filename: str, content: str) -> Path:
    """data/reports/<filename> に保存し、標準出力にも流す。保存先 Path を返す。"""
    out_dir = repo_root() / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(content, encoding="utf-8")
    print(content)
    print(f"[saved] {out_path}")
    return out_path
