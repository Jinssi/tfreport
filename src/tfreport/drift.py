"""Drift report.

Run scheduled in CI as:

    terraform plan -refresh-only -out=tfdrift -no-color
    terraform show -json tfdrift > drift.json
    tf-report-drift drift.json --out drift_summary.md \\
        --history reports/drift-history.jsonl

The output is a plan summary tailored to drift: any changes returned by a
refresh-only plan are real-world drift from desired state.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config, load as load_config
from .plan import parse_plan, render_markdown


def _drift_heading(summary_dict: dict[str, Any]) -> str:
    s = summary_dict.get("stats", {})
    n = int(s.get("total_changing", 0) or 0)
    return f"Drift detected ({n} resource(s))" if n else "No drift detected"


def append_history(path: str | Path, summary: dict[str, Any]) -> None:
    s = summary.get("stats", {})
    rb = summary.get("risks_by_severity", {})
    entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "drifted": int(s.get("total_changing", 0) or 0),
        "stats": s,
        "risks_by_severity": rb,
        "ci": (summary.get("provenance") or {}).get("ci", {}),
        "tfreport_version": (summary.get("provenance") or {}).get("tfreport_version", ""),
        "addresses": [c.get("address") for c in (summary.get("changes") or [])],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Summarise a refresh-only Terraform plan as a drift report."
    )
    p.add_argument("plan", help="Path to refresh-only plan JSON, or '-' for stdin.")
    p.add_argument("--out", default="drift_summary.md")
    p.add_argument("--json-out", default=None)
    p.add_argument("--config", default=None)
    p.add_argument(
        "--history",
        default=None,
        help="Append a JSONL drift entry to this file (always).",
    )
    args = p.parse_args(argv)

    try:
        if args.plan == "-":
            data = sys.stdin.read()
        else:
            with open(args.plan, "r", encoding="utf-8") as f:
                data = f.read()
        plan = json.loads(data)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: failed to read plan JSON: {e}", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
    except (OSError, RuntimeError, FileNotFoundError) as e:
        print(f"warning: config load failed: {e}", file=sys.stderr)
        config = Config()

    summary = parse_plan(plan, config=config)
    summary_dict = summary.to_dict()

    md = render_markdown(summary, config=config, heading=_drift_heading(summary_dict))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(summary_dict, f, indent=2)

    if args.history:
        try:
            append_history(args.history, summary_dict)
        except OSError as e:
            print(f"warning: failed to append drift history: {e}", file=sys.stderr)

    drifted = summary.stats["total_changing"]
    print(f"wrote {args.out} (drift={drifted})")
    # Exit 0 always (advisory). Consumers can `jq` the history JSON to alert.
    return 0


if __name__ == "__main__":
    sys.exit(main())
