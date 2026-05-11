"""Tests for summarize_apply."""

from __future__ import annotations

from pathlib import Path

from tfreport import apply as summarize_apply


SUCCESS_LOG = """\
azurerm_resource_group.this: Creating...
azurerm_resource_group.this: Creation complete after 5s [id=/subscriptions/x]
azurerm_virtual_network.hub: Modifying...
azurerm_virtual_network.hub: Modifications complete after 12s [id=/subscriptions/x]

Apply complete! Resources: 1 added, 1 changed, 0 destroyed.
"""


FAILURE_LOG = """\
azurerm_resource_group.this: Creating...
azurerm_storage_account.data: Creating...

Error: creating Storage Account: unexpected status 409

  with azurerm_storage_account.data,
  on main.tf line 12, in resource "azurerm_storage_account" "data":
  12: resource "azurerm_storage_account" "data" {

The storage account name is already in use.


"""


def test_parses_success_log():
    r = summarize_apply.parse_apply_log(SUCCESS_LOG)
    assert r.succeeded is True
    assert r.added == 1
    assert r.changed == 1
    assert r.destroyed == 0
    assert r.errors == []
    addrs = {x["address"] for x in r.per_resource}
    assert "azurerm_resource_group.this" in addrs
    assert "azurerm_virtual_network.hub" in addrs


def test_parses_failure_log():
    r = summarize_apply.parse_apply_log(FAILURE_LOG)
    assert r.succeeded is False
    assert len(r.errors) == 1
    assert "Storage Account" in r.errors[0]


def test_render_includes_planned_vs_applied():
    r = summarize_apply.parse_apply_log(SUCCESS_LOG)
    md = summarize_apply.render_markdown(
        r,
        plan_summary={"stats": {"create": 1, "update": 1, "delete": 0, "replace": 0}},
    )
    assert "## Planned vs Applied" in md
    assert "Apply succeeded" in md


def test_render_failure_section():
    r = summarize_apply.parse_apply_log(FAILURE_LOG)
    md = summarize_apply.render_markdown(r)
    assert "did not complete cleanly" in md
    assert "## Errors" in md


def test_main_exit_codes(tmp_path: Path):
    log = tmp_path / "apply.log"
    log.write_text(SUCCESS_LOG, encoding="utf-8")
    out = tmp_path / "apply.md"
    rc = summarize_apply.main(["--log", str(log), "--out", str(out)])
    assert rc == 0
    assert out.exists()

    log.write_text(FAILURE_LOG, encoding="utf-8")
    rc = summarize_apply.main(["--log", str(log), "--out", str(out)])
    assert rc == 1
