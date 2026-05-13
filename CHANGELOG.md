# Changelog

All notable changes to **tfreport** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-05-14

First public release. Plan reports gain a comprehensive, visualization-first
layout aimed at deployers reviewing real Azure changes.

### Added

- **Dashboard** at the top of every plan report: action-mix Mermaid pie,
  risk-severity Mermaid bar, and Unicode-bar impact heatmap.
- **Module map** Mermaid flowchart in the executive summary, colored by max
  aggregated severity per module.
- **Blast radius** analyzer (`graph.py`) — builds a dependency graph from
  `configuration.root_module` references and `depends_on`; surfaces
  `blast_radius_score` per change and a `## Blast radius` section that lists
  destructive ops with the downstream resources they would impact.
- **Policy body diff analyzer** (`policy.py`) — for `azurerm_policy_*` and
  `azuread_conditional_access_policy`: detects effect transitions
  (`Audit → Deny`, etc.), parameter deltas, and scope changes. Effect
  tightening upgrades risk severity to **high**.
- **Compliance checks** (`compliance.py`) — pluggable rule packs configured
  in `.tfreport.json` under `compliance:` (`required_tags`, `naming`,
  `allowed_regions`, `no_public_network`, `encryption_required`). Renders a
  `## Compliance checks` section with a pass/fail score.
- **Cost visualization** — `## Cost impact` now includes a Mermaid
  xychart-beta bar of top monthly deltas alongside the existing table.
- **History & trend** (`history.py` + `--history PATH`) — appends a compact
  JSONL snapshot per run and renders `## Trend (last N runs)` with Unicode
  sparkbars for change-count, risks, cost delta, and compliance score.
- **Apply dashboard** + **Apply gantt** — `apply.py` now parses
  Terraform-style durations from log lines and renders an apply dashboard
  badge row, a slowest-resources table, and a Mermaid `gantt` of the top-20
  longest operations.
- **Multi-format exporters** (`exporters/`):
  - `--html-out` — self-contained HTML report with inline CSS and SVG bars.
  - `--sarif-out` — SARIF 2.1.0 for GitHub Code Scanning, including risks
    and compliance findings.
  - `--teams-out` — Microsoft Teams Adaptive Card v1.4 envelope.
  - `--slack-out` — Slack Block Kit payload.
  - `--report-link` to embed a URL into Teams/Slack messages.
- **Routing** (`routing.py`) — `.tfreport.json → routing.rules` maps changes
  to suggested reviewers via address or resource-type globs. Rendered as a
  `## Suggested reviewers` table.
- **Rollback playbook** (`rollback.py`) — for each destructive change on a
  stateful resource (storage, SQL, PostgreSQL, Cosmos, Key Vault, VM, AKS,
  etc.) emits per-resource **Pre-checks** and **Rollback steps**.

### Changed

- `ResourceChange` gained `blast_radius_score`, `downstream`, and
  `after_state` fields. `PlanSummary` gained `policy_changes` and
  `compliance` fields exposed via `to_dict()`.
- Plan report section order is now: Dashboard → Executive summary (with
  Module map) → Blast radius → Policy changes → Compliance checks →
  Trend → Stats → Compared to baseline → Cost impact → Changes → Risks →
  Suggested reviewers → Rollback playbook → Resource details → Narrative.

### Notes

- Zero new runtime dependencies. All visualizations are produced as Mermaid
  diagrams, Unicode glyphs, or inline SVG/HTML.

## [0.4.1] - 2026-05-13

### Added

- `scripts/gh-actions-cleanup.ps1` - helper script for purging GitHub Actions run history and artifacts via `gh` CLI, with support for dry runs and keeping the newest runs.

### Changed

- The tag release workflow no longer uses `upload-artifact` / `download-artifact`; each release job now rebuilds distributions locally, which avoids GitHub Actions artifact-storage quota from blocking GitHub Releases and PyPI publishing.
- README now documents the release-maintenance path and the cleanup helper script.

## [0.4.0] - 2026-05-12

### Added

- **Executive summary** - plan reports now open with a deterministic admin-first summary that highlights what changed, what to review first, where the operational impact is, and which modules are hotspots.
- **Operational domain grouping** - changing resources are aggregated into identity, network, compute, data, monitoring, and platform domains to make the big picture easier to assess.
- `PlanSummary.admin_summary` - JSON output now includes the same high-level reviewer model used by the Markdown renderer, so downstream PR comments and other consumers can reuse it without scraping Markdown.

### Changed

- `render_markdown()` now renders the executive summary before the raw stats and detailed change tables.
- Optional LLM narrative prompt now uses the deterministic admin summary as its source of truth and focuses on reviewer guidance instead of repeating counts.
- `examples/sample-report.md` and `examples/sample-report-avm.md` refreshed to demonstrate the new admin-first layout.

## [0.3.0] - 2026-05-11

### Added

- **List-element diff** - for changed list attributes whose elements are scalars (e.g. `address_space`, `address_prefixes`, `dns_servers`, `allowed_ip_ranges`), reports now render `added` / `removed` element bullets in *Resource details* instead of dumping the full list blob.
- **Keyed nested-block diff** - for known list-of-objects attributes (NSG `security_rule`, firewall `network_rule` / `application_rule` / `nat_rule`, route-table `route`, Key Vault `access_policy`, storage `ip_rule` / `virtual_network_rule`, identity `role_assignment`), rules are matched by their natural key (`name`, `object_id`, `principal_id`, …) and *added / removed / changed* rules are listed individually. Changed rules show per-attribute before → after.
- `tfreport.diff.list_element_diff()` and `tfreport.diff.keyed_block_diff()` helpers, plus `KEYED_BLOCK_ATTRS` registry for extension.

### Changed

- `ResourceChange` dataclass gains `list_diffs: dict[str, dict]` and `block_diffs: dict[str, dict]` (serialised to JSON summary).
- `examples/sample-report.md` refreshed to include NSG rule-diff output.

## [0.2.1] - 2026-05-11

### Added

- **Per-attribute before → after table** in the *Resource details* section for `update` and `replace` actions. Long values are truncated; secret-looking keys (`*password*`, `*secret*`, `*token*`, `*key*`, `*credential*`, `*connection_string*`, `*sas*`) and Terraform's own `before_sensitive` / `after_sensitive` flags are masked as `_(sensitive)_`.
- `tfreport.diff.attr_diffs(before, after, sensitive_before, sensitive_after)` helper exposed alongside `changed_top_level_keys`.
- `examples/sample-report.md` - full rendered output against an AVM AI/ML Landing Zone plan, committed for reference.

### Changed

- `ResourceChange` dataclass adds `attr_diffs: list[dict[str, str]]` (also serialised to JSON summary).
- README "What the report contains" lists the new sections and links to the sample.

## [0.2.0] - 2026-05-11

### Added

- **`tf-report-drift` CLI** - render drift detection reports from `terraform plan` JSON, with its own JSONL history ledger.
- **Baseline comparison** - `--baseline plan_summary.json` shows stat delta and *new* HIGH-severity risks vs a prior run.
- **Cost overlay** - `--cost-json infracost.json` adds monthly cost Δ, total, and top changed resources.
- **Config file** - `.tfreport.yml` (auto-discovered up to the nearest `.git`) with `ignore` globs, `group_by_module`, `demote_tag_only`, and `diff_details`. Wire via `--config` or env. Install `tfreport[full]` for PyYAML.
- **`--heading`** - custom H2 heading on plan / drift reports for per-environment runs.
- **History ledger** - `--history reports/*.jsonl` appends one JSON line per apply / drift run for trend reporting.
- **Module grouping & tag-only demotion** - changes grouped by `module_address`; tag-only updates collapsed into a `<details>` block.
- **Replace_paths surfacing** - per-resource detail shows which attributes forced a replace.
- **Provenance footer** - Terraform version, tfreport version, ISO-8601 timestamp, and CI source (`github-actions` / `azure-devops` / `gitlab-ci` / `local`).
- **`tfreport[full]` extra** - pulls in PyYAML for config files.
- **Action / workflow inputs** - `action.yml` and both reusable workflows now accept `config`, `baseline-artifact`, `cost-json`, `heading`, `install-extras`, and (apply) `history`.

### Changed

- `parse_plan(plan, config=None)` now accepts a `Config` and respects `ignore` globs.
- `render_markdown(summary, *, config, narrative, delta, cost, heading)` - keyword-only options for delta/cost/heading.
- `ResourceChange` dataclass extended with `module`, `risks`, `changed_attrs`, `replace_paths`, `tag_only`, `ignored`.

### Removed

- Dead code: `diff.short_name()` and the unused `_RESOURCE_TAIL_RE` regex.
- Unused `Config.max_rows` field and `--max-rows` CLI flag (never read by renderer).
- Misleading provenance docstring claiming `state.serial`/`lineage` capture.

### Tests

- 24 unit tests across plan, apply, and Tier 1–3 features (config, diff, delta, cost, drift, history, provenance, risk rules). All green.
- End-to-end smoke verified against the AVM AI/ML Landing Zone module structure (`terraform-azurerm-avm-ptn-aiml-landing-zone/examples/default`).

## [0.1.0] - Initial release

- `tf-report-plan` and `tf-report-apply` CLIs.
- Risk classifier with high / medium / low rules for stateful resources, identity, network, policy.
- Optional LLM narrative (GitHub Models or Azure OpenAI).
- GitHub composite action + reusable workflows (`terraform-plan.yml`, `terraform-apply.yml`).
- Apache-2.0 license.

[0.4.1]: https://github.com/Jinssi/tfreport/releases/tag/v0.4.1
[0.4.0]: https://github.com/Jinssi/tfreport/releases/tag/v0.4.0
[0.2.0]: https://github.com/Jinssi/tfreport/releases/tag/v0.2.0
[0.1.0]: https://github.com/Jinssi/tfreport/releases/tag/v0.1.0
