"""Unit tests for src/agents/methodologies/__init__.py — methodology loader.

Verifies that the methodology Markdown files are present, readable, and
correctly loaded into the 3 PM agent modules at import time.
"""
from pathlib import Path

import pytest

from src.agents import methodologies
from src.agents.methodologies import load_methodology


METHODOLOGIES_DIR = Path(methodologies.__file__).parent


class TestLoadMethodology:
    def test_loads_existing_file(self):
        # warren_buffett.md must exist (Phase F-1 deliverable)
        text = load_methodology("warren_buffett")
        assert text != ""
        assert len(text) > 1_000  # at least 1k chars of methodology

    def test_loads_druckenmiller(self):
        text = load_methodology("stanley_druckenmiller")
        assert text != ""
        assert len(text) > 1_000

    def test_loads_munger(self):
        text = load_methodology("charlie_munger")
        assert text != ""
        assert len(text) > 1_000

    def test_missing_file_returns_empty(self):
        # lru_cache could mask the test on retries; call cache_clear first
        load_methodology.cache_clear()
        text = load_methodology("totally_nonexistent_agent")
        assert text == ""

    def test_lru_cache_works(self):
        load_methodology.cache_clear()
        first = load_methodology("warren_buffett")
        second = load_methodology("warren_buffett")
        # Same object identity == cache hit
        assert first is second


class TestMethodologyFileContents:
    """Sanity checks that the methodology files contain expected concepts."""

    def test_buffett_mentions_4_filters(self):
        text = load_methodology("warren_buffett")
        # Methodology should reference 4 Filters framework
        assert ("4 Filters" in text) or ("Four Filters" in text) or ("4つのフィルター" in text)

    def test_buffett_mentions_margin_of_safety(self):
        text = load_methodology("warren_buffett")
        assert ("Margin of Safety" in text) or ("安全マージン" in text)

    def test_buffett_mentions_jp_section(self):
        # E:\Company's methodology has a JP-specific section
        text = load_methodology("warren_buffett")
        assert ("商社" in text) or ("日本株" in text) or ("Japan" in text)

    def test_druckenmiller_mentions_asymmetric(self):
        text = load_methodology("stanley_druckenmiller")
        assert ("asymmetric" in text.lower()) or ("非対称" in text)

    def test_munger_mentions_mental_models(self):
        text = load_methodology("charlie_munger")
        assert "Mental Model" in text or "Lattice" in text

    def test_munger_mentions_invert(self):
        text = load_methodology("charlie_munger")
        assert "Invert" in text

    def test_munger_mentions_lollapalooza(self):
        text = load_methodology("charlie_munger")
        assert "Lollapalooza" in text


class TestAgentModulesLoadMethodologyAtImport:
    """Verify methodology is injected at module import (used in system prompts)."""

    def test_buffett_agent_has_methodology(self):
        from src.agents.warren_buffett import _BUFFETT_METHODOLOGY
        assert _BUFFETT_METHODOLOGY != ""
        assert len(_BUFFETT_METHODOLOGY) > 1_000

    def test_druckenmiller_agent_has_methodology(self):
        from src.agents.stanley_druckenmiller import _DRUCK_METHODOLOGY
        assert _DRUCK_METHODOLOGY != ""
        assert len(_DRUCK_METHODOLOGY) > 1_000

    def test_munger_agent_has_methodology(self):
        from src.agents.charlie_munger import _MUNGER_METHODOLOGY
        assert _MUNGER_METHODOLOGY != ""
        assert len(_MUNGER_METHODOLOGY) > 1_000


class TestMethodologyDirStructure:
    def test_dir_exists(self):
        assert METHODOLOGIES_DIR.is_dir()

    def test_expected_files_present(self):
        for agent in ("warren_buffett", "stanley_druckenmiller", "charlie_munger"):
            path = METHODOLOGIES_DIR / f"{agent}.md"
            assert path.exists(), f"Missing methodology file: {path}"
            assert path.stat().st_size > 1_000, f"Suspiciously small: {path}"
