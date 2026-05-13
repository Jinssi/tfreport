"""Resource dependency graph + blast-radius analysis.

Builds a directed dependency graph from a Terraform plan JSON's
``configuration`` section. Each edge ``A -> B`` means resource A depends on
resource B (B is upstream of A). ``downstream_of(addr)`` therefore returns
every resource that would be impacted if ``addr`` is destroyed or replaced.

Plans that omit ``configuration`` (e.g. some hand-crafted fixtures or older
Terraform versions) yield an empty graph; callers should handle that
gracefully.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DependencyGraph:
    # node -> set of nodes it depends on (upstream)
    upstream: dict[str, set[str]] = field(default_factory=dict)
    # node -> set of nodes that depend on it (downstream)
    downstream: dict[str, set[str]] = field(default_factory=dict)
    nodes: set[str] = field(default_factory=set)

    def add_edge(self, dependent: str, dependency: str) -> None:
        if not dependent or not dependency or dependent == dependency:
            return
        self.nodes.add(dependent)
        self.nodes.add(dependency)
        self.upstream.setdefault(dependent, set()).add(dependency)
        self.downstream.setdefault(dependency, set()).add(dependent)

    def add_node(self, addr: str) -> None:
        if addr:
            self.nodes.add(addr)

    def upstream_of(self, addr: str) -> set[str]:
        return set(self.upstream.get(addr, ()))

    def downstream_of(self, addr: str) -> set[str]:
        return set(self.downstream.get(addr, ()))

    def blast_radius(self, addr: str) -> set[str]:
        """All resources transitively downstream of ``addr`` (excluding self)."""
        seen: set[str] = set()
        queue = deque(self.downstream.get(addr, ()))
        while queue:
            node = queue.popleft()
            if node in seen or node == addr:
                continue
            seen.add(node)
            queue.extend(self.downstream.get(node, ()))
        return seen


# -- Configuration walker -----------------------------------------------------


def _expression_references(value: Any) -> Iterable[str]:
    """Yield reference strings from a configuration expression subtree."""
    if isinstance(value, Mapping):
        refs = value.get("references")
        if isinstance(refs, list):
            for r in refs:
                if isinstance(r, str):
                    yield r
        for v in value.values():
            if v is value:
                continue
            yield from _expression_references(v)
    elif isinstance(value, list):
        for item in value:
            yield from _expression_references(item)


def _normalize_ref(ref: str, module_prefix: str) -> str | None:
    """Convert a configuration reference into a fully-qualified address.

    Configuration references look like ``azurerm_subnet.app`` or
    ``module.networking.azurerm_subnet.app`` (the latter when the reference is
    to a module output). Local variables, providers, and ``each.*``/``count.*``
    expressions are filtered out.
    """
    if not ref:
        return None
    # Strip attribute accessors / index suffixes.
    head = ref.split("[", 1)[0]
    parts = head.split(".")
    if not parts:
        return None
    first = parts[0]
    if first in {"var", "local", "each", "count", "self", "path", "data", "terraform"}:
        return None
    if first == "module":
        # module.<name>.<output> — we can only assert dependency on the module,
        # not a specific resource. Skip.
        return None
    if len(parts) < 2:
        return None
    addr = f"{parts[0]}.{parts[1]}"
    if module_prefix:
        addr = f"{module_prefix}.{addr}"
    return addr


def _walk_module(
    module: Mapping[str, Any],
    module_prefix: str,
    graph: DependencyGraph,
) -> None:
    resources = module.get("resources") or []
    for res in resources:
        if not isinstance(res, Mapping):
            continue
        addr = res.get("address")
        if not isinstance(addr, str):
            continue
        full_addr = f"{module_prefix}.{addr}" if module_prefix else addr
        graph.add_node(full_addr)
        # depends_on declared explicitly.
        depends_on = res.get("depends_on") or []
        for dep in depends_on:
            normalized = _normalize_ref(str(dep), module_prefix)
            if normalized:
                graph.add_edge(full_addr, normalized)
        # Implicit references inside expressions.
        for ref in _expression_references(res.get("expressions")):
            normalized = _normalize_ref(ref, module_prefix)
            if normalized:
                graph.add_edge(full_addr, normalized)
    module_calls = module.get("module_calls") or {}
    if isinstance(module_calls, Mapping):
        for name, call in module_calls.items():
            if not isinstance(call, Mapping):
                continue
            sub = call.get("module")
            if not isinstance(sub, Mapping):
                continue
            sub_prefix = f"{module_prefix}.module.{name}" if module_prefix else f"module.{name}"
            _walk_module(sub, sub_prefix, graph)


def build_graph(plan: Mapping[str, Any]) -> DependencyGraph:
    """Build a dependency graph from a Terraform plan JSON."""
    graph = DependencyGraph()
    config = plan.get("configuration")
    if isinstance(config, Mapping):
        root = config.get("root_module")
        if isinstance(root, Mapping):
            _walk_module(root, "", graph)
    # Make sure every changing resource is a node even if it has no edges.
    for rc in plan.get("resource_changes") or []:
        addr = rc.get("address") if isinstance(rc, Mapping) else None
        if isinstance(addr, str):
            graph.add_node(addr)
    return graph


# -- Helpers used by rendering ------------------------------------------------


def blast_radius_summary(
    graph: DependencyGraph,
    addresses: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """For each address, return its downstream set and score."""
    out: dict[str, dict[str, Any]] = {}
    for addr in addresses:
        downstream = graph.blast_radius(addr)
        out[addr] = {
            "downstream": sorted(downstream),
            "score": len(downstream),
        }
    return out
