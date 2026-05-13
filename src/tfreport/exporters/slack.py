"""Slack Block Kit exporter for plan summaries.

Output is a payload suitable for POST to a Slack Incoming Webhook URL.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def render(summary: Mapping[str, Any], *, title: str = "Terraform Plan Report", link: str | None = None) -> dict[str, Any]:
    stats = summary.get("stats") or {}
    risks_by_sev = summary.get("risks_by_severity") or {}
    risks = summary.get("risks") or []
    compliance = summary.get("compliance") or {}

    score_line = ""
    if compliance.get("enabled") and compliance.get("score") is not None:
        score_line = f"  •  *Compliance:* {int(compliance['score'] * 100)}%"

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Create:* {stats.get('create', 0)}"},
                {"type": "mrkdwn", "text": f"*Update:* {stats.get('update', 0)}"},
                {"type": "mrkdwn", "text": f"*Delete:* {stats.get('delete', 0)}"},
                {"type": "mrkdwn", "text": f"*Replace:* {stats.get('replace', 0)}"},
                {"type": "mrkdwn", "text": f"*High:* {risks_by_sev.get('high', 0)}"},
                {"type": "mrkdwn", "text": f"*Medium:* {risks_by_sev.get('medium', 0)}"},
            ],
        },
    ]
    if score_line:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": score_line}]})

    top_risks = [r for r in risks if (r.get("severity") or "").lower() == "high"][:5]
    if top_risks:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Top high-severity risks*"},
            }
        )
        for r in top_risks:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• *{r.get('rule_id', '')}*: `{r.get('address', '')}`\n{r.get('message', '')}",
                    },
                }
            )

    if link:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open report"},
                        "url": link,
                    }
                ],
            }
        )

    return {"blocks": blocks}


def render_str(summary: Mapping[str, Any], **kwargs: Any) -> str:
    return json.dumps(render(summary, **kwargs), indent=2)
