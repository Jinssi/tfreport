"""Tests for history snapshot/append/render."""

from __future__ import annotations

import json
from pathlib import Path

from tfreport import history
from tfreport import plan as summarize_plan

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


def test_snapshot_shape():
    summary = summarize_plan.parse_plan(_load("mixed.json"))
    snap = history.snapshot(summary.to_dict(), cost_diff=12.34)
    assert "timestamp" in snap
    assert snap["stats"]["total_changing"] == 5
    assert snap["cost_diff_monthly"] == 12.34


def test_append_and_load_roundtrip(tmp_path: Path):
    file = tmp_path / "hist.jsonl"
    history.append(file, {"timestamp": "t1", "stats": {"total_changing": 1}, "risks": {"high": 0, "medium": 0, "low": 0}})
    history.append(file, {"timestamp": "t2", "stats": {"total_changing": 2}, "risks": {"high": 1, "medium": 0, "low": 0}})
    entries = history.load(file)
    assert len(entries) == 2
    assert entries[-1]["stats"]["total_changing"] == 2


def test_render_section_returns_empty_for_single_entry():
    assert history.render_section([{"stats": {}, "risks": {}}]) == []


def test_render_section_with_5_entries():
    entries = [
        {"stats": {"total_changing": i}, "risks": {"high": 0, "medium": 0, "low": 0}, "cost_diff_monthly": float(i)}
        for i in range(5)
    ]
    out = "\n".join(history.render_section(entries))
    assert "## Trend (last 5 runs)" in out
    assert "Changes" in out


def test_cli_with_history(tmp_path: Path):
    file = tmp_path / "hist.jsonl"
    out_md = tmp_path / "out.md"
    # Run twice so trend section can render.
    for _ in range(2):
        rc = summarize_plan.main([
            str(FIXTURES / "mixed.json"),
            "--out", str(out_md),
            "--history", str(file),
        ])
        assert rc == 0
    md = out_md.read_text(encoding="utf-8")
    assert "## Trend" in md
    assert file.exists()
    assert sum(1 for _ in file.open()) == 2
