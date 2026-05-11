"""tfreport — Terraform plan/apply/drift report generator.

CLI entry points:
  tf-report-plan   → tfreport.plan:main
  tf-report-apply  → tfreport.apply:main
  tf-report-drift  → tfreport.drift:main
"""

__version__ = "0.2.1"

from .apply import parse_apply_log, render_markdown as render_apply_markdown
from .plan import parse_plan, render_markdown as render_plan_markdown

__all__ = [
    "parse_plan",
    "render_plan_markdown",
    "parse_apply_log",
    "render_apply_markdown",
    "__version__",
]
