# Changelog

All notable changes to **tfreport** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-05-11

### Added

- **`tf-report-drift` CLI** — render drift detection reports from `terraform plan` JSON, with its own JSONL history ledger.
- **Baseline comparison** — `--baseline plan_summary.json` shows stat delta and *new* HIGH-severity risks vs a prior run.
- **Cost overlay** — `--cost-json infracost.json` adds monthly cost Δ, total, and top changed resources.
- **Config file** — `.tfreport.yml` (auto-discovered up to the nearest `.git`) with `ignore` globs, `group_by_module`, `demote_tag_only`, and `diff_details`. Wire via `--config` or env. Install `tfreport[full]` for PyYAML.
- **`--heading`** — custom H2 heading on plan / drift reports for per-environment runs.
- **History ledger** — `--history reports/*.jsonl` appends one JSON line per apply / drift run for trend reporting.
- **Module grouping & tag-only demotion** — changes grouped by `module_address`; tag-only updates collapsed into a `<details>` block.
- **Replace_paths surfacing** — per-resource detail shows which attributes forced a replace.
- **Provenance footer** — Terraform version, tfreport version, ISO-8601 timestamp, and CI source (`github-actions` / `azure-devops` / `gitlab-ci` / `local`).
- **`tfreport[full]` extra** — pulls in PyYAML for config files.
- **Action / workflow inputs** — `action.yml` and both reusable workflows now accept `config`, `baseline-artifact`, `cost-json`, `heading`, `install-extras`, and (apply) `history`.

### Changed

- `parse_plan(plan, config=None)` now accepts a `Config` and respects `ignore` globs.
- `render_markdown(summary, *, config, narrative, delta, cost, heading)` — keyword-only options for delta/cost/heading.
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

[0.2.0]: https://github.com/Jinssi/terraformer/releases/tag/v0.2.0
[0.1.0]: https://github.com/Jinssi/terraformer/releases/tag/v0.1.0
