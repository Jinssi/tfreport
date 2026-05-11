"""Optional .tfreport configuration loader.

Auto-discovers .tfreport.json (always) or .tfreport.yml/.tfreport.yaml (if PyYAML
is installed) in the current working directory or any explicitly given path.

Schema (all keys optional):

    ignore:                       # list of glob patterns matched against resource address
      - "module.legacy.*"
      - "azurerm_app_service_custom_hostname_binding.*"
    group_by_module: true         # group changes table by module path
    demote_tag_only: true         # collapse tag-only updates into a separate section
    diff_details: true            # render per-resource <details> blocks
    ai: false                     # default for --ai flag (CLI overrides)

Anything not recognised is ignored (forward-compatible).
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Config:
    ignore: list[str] = field(default_factory=list)
    group_by_module: bool = True
    demote_tag_only: bool = True
    diff_details: bool = True
    ai: bool = False
    source: str | None = None  # path the config was loaded from, for the footer

    def is_ignored(self, address: str) -> bool:
        return any(fnmatch.fnmatchcase(address, pat) for pat in self.ignore)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str | None = None) -> "Config":
        c = cls(source=source)
        if not isinstance(data, dict):
            return c
        if isinstance(data.get("ignore"), list):
            c.ignore = [str(x) for x in data["ignore"]]
        for key in ("group_by_module", "demote_tag_only", "diff_details", "ai"):
            if key in data:
                c.__setattr__(key, bool(data[key]))
        return c


def _try_yaml(path: Path) -> dict[str, Any] | None:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    data = _try_yaml(path)
    if data is None:
        raise RuntimeError(
            f"PyYAML is not installed; install with `pip install tfreport[full]` "
            f"or convert {path.name} to .tfreport.json."
        )
    return data


def discover(start: str | os.PathLike[str] | None = None) -> Config:
    """Walk up from `start` looking for a config file. Returns defaults if none."""
    here = Path(start).resolve() if start else Path.cwd()
    if here.is_file():
        here = here.parent
    candidates = (".tfreport.yml", ".tfreport.yaml", ".tfreport.json")
    for parent in (here, *here.parents):
        for name in candidates:
            p = parent / name
            if p.is_file():
                return Config.from_dict(_load_file(p), source=str(p))
        if (parent / ".git").exists():
            break
    return Config()


def load(path: str | os.PathLike[str] | None) -> Config:
    if not path:
        return discover()
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    return Config.from_dict(_load_file(p), source=str(p))
