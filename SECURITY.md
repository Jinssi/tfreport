# Security Policy

## Reporting

Please report suspected vulnerabilities privately via GitHub Security Advisories on this repository, **not** as public issues.

## Scope

`tfreport` is a report generator. It reads Terraform plan JSON and apply logs and produces Markdown. It does **not**:

- run Terraform itself,
- modify Terraform state,
- transmit plan content to third parties unless `--ai` is enabled.

When `--ai` is enabled, the deterministic JSON summary (not the raw plan) is sent to the configured LLM backend (GitHub Models or Azure OpenAI). Review your organisation's data-handling policy before enabling.
