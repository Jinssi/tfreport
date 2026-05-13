"""Tests for multi-format exporters."""

from __future__ import annotations

import json
from pathlib import Path

from tfreport import plan as summarize_plan
from tfreport.exporters import html as html_exp
from tfreport.exporters import sarif as sarif_exp
from tfreport.exporters import slack as slack_exp
from tfreport.exporters import teams as teams_exp

FIXTURES = Path(__file__).parent / "fixtures"


def _summary() -> dict:
    with open(FIXTURES / "mixed.json", "r", encoding="utf-8") as f:
        plan = json.load(f)
    return summarize_plan.parse_plan(plan).to_dict()


def test_html_render_contains_badges_and_title():
    out = html_exp.render(_summary(), title="My Plan")
    assert "<!DOCTYPE html>" in out
    assert "My Plan" in out
    assert "b-create" in out
    assert "Action mix" in out


def test_sarif_render_shape():
    obj = sarif_exp.render(_summary(), version="9.9.9")
    assert obj["version"] == "2.1.0"
    assert obj["runs"][0]["tool"]["driver"]["name"] == "tfreport"
    assert obj["runs"][0]["tool"]["driver"]["version"] == "9.9.9"
    # Results may be empty if mixed.json has no risks, but the run must exist.
    assert isinstance(obj["runs"][0]["results"], list)


def test_teams_card_shape():
    obj = teams_exp.render(_summary(), title="X", link="https://example.com/r")
    assert obj["type"] == "message"
    card = obj["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    actions = card.get("actions") or []
    assert any(a.get("url") == "https://example.com/r" for a in actions)


def test_slack_blocks_shape():
    obj = slack_exp.render(_summary(), title="X", link="https://example.com/r")
    assert "blocks" in obj
    assert obj["blocks"][0]["type"] == "header"
    # Contains the report-link button
    assert any(b.get("type") == "actions" for b in obj["blocks"])


def test_cli_writes_all_exporter_files(tmp_path: Path):
    out_md = tmp_path / "report.md"
    html_out = tmp_path / "report.html"
    sarif_out = tmp_path / "report.sarif"
    teams_out = tmp_path / "teams.json"
    slack_out = tmp_path / "slack.json"
    rc = summarize_plan.main([
        str(FIXTURES / "mixed.json"),
        "--out", str(out_md),
        "--html-out", str(html_out),
        "--sarif-out", str(sarif_out),
        "--teams-out", str(teams_out),
        "--slack-out", str(slack_out),
        "--report-link", "https://example.com/r",
    ])
    assert rc == 0
    assert html_out.exists() and "<!DOCTYPE html>" in html_out.read_text(encoding="utf-8")
    sarif_obj = json.loads(sarif_out.read_text(encoding="utf-8"))
    assert sarif_obj["version"] == "2.1.0"
    teams_obj = json.loads(teams_out.read_text(encoding="utf-8"))
    assert teams_obj["type"] == "message"
    slack_obj = json.loads(slack_out.read_text(encoding="utf-8"))
    assert "blocks" in slack_obj
