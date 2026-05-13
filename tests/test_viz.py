"""Tests for the deterministic visualization helpers."""

from __future__ import annotations

import json
from pathlib import Path

from tfreport import plan as summarize_plan
from tfreport import viz

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


def test_sparkbar_handles_empty_and_zero():
    assert viz.sparkbar([]) == ""
    assert viz.sparkbar([0, 0, 0]) == "▁▁▁"


def test_sparkbar_scales_to_max():
    s = viz.sparkbar([1, 4, 8])
    assert len(s) == 3
    # First tick must be <= last tick.
    assert s[0] <= s[-1]


def test_severity_and_action_badges():
    assert viz.severity_badge("high") == "🟥"
    assert viz.severity_badge(None) == "🟩"
    assert viz.action_badge("delete") == "🔴"
    assert viz.action_badge("unknown-action") == "⚪"


def test_mermaid_pie_drops_zero_slices():
    out = viz.mermaid_pie("t", {"a": 0, "b": 3})
    body = "\n".join(out)
    assert "```mermaid" in body
    assert '"b"' in body
    assert '"a"' not in body


def test_mermaid_pie_empty_returns_empty():
    assert viz.mermaid_pie("t", {"a": 0, "b": 0}) == []


def test_mermaid_bar_caps_rows():
    rows = [(f"r{i}", float(i + 1)) for i in range(20)]
    out = "\n".join(viz.mermaid_bar("t", rows))
    # Should not contain all 20 labels.
    assert "r0" in out
    assert "r19" not in out


def test_dashboard_renders_for_mixed():
    summary = summarize_plan.parse_plan(_load("mixed.json"))
    md = summarize_plan.render_markdown(summary)
    assert "## Dashboard" in md
    assert "```mermaid" in md
    # Headline badges present.
    assert "change(s)" in md
    # Dashboard appears before Executive summary.
    assert md.index("## Dashboard") < md.index("## Executive summary")


def test_dashboard_skipped_on_noop():
    summary = summarize_plan.parse_plan(_load("noop.json"))
    md = summarize_plan.render_markdown(summary)
    assert "## Dashboard" not in md


def test_module_map_present_with_modules():
    summary = summarize_plan.parse_plan(_load("with_modules.json"))
    md = summarize_plan.render_markdown(summary)
    if "### Module hotspots" in md:
        assert "#### Module map" in md
        assert "flowchart TD" in md
