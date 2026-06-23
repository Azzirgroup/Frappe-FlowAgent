# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
Node executor registry.

Each node type (`trigger_doctype`, `ai_llm`, `frappe_create`, ...) has a
corresponding executor class that knows how to run that node. Executors
are registered via the `@node` decorator and dispatched by name from the
Runner.

To add a new node type:

    @node("my_new_type")
    class MyExecutor(BaseExecutor):
        def run(self, *, node, cfg, context, runner):
            ...
            return result              # default port
            return ("out-yes", value)  # specific port
            return SKIP                # terminate this branch
"""

from __future__ import annotations

from typing import Any


_REGISTRY: dict[str, "BaseExecutor"] = {}


class BaseExecutor:
    """Base class for all node executors. Override `run`."""

    def run(self, *, node: dict, cfg: dict, context, runner) -> Any:
        raise NotImplementedError


def node(type_name: str):
    """Decorator that registers an executor class against a node type."""
    def deco(cls):
        _REGISTRY[type_name] = cls()
        cls._flowagent_type = type_name
        return cls
    return deco


def get_executor(type_name: str) -> "BaseExecutor | None":
    return _REGISTRY.get(type_name)


def all_node_types() -> list[str]:
    return sorted(_REGISTRY.keys())


# Importing the modules below populates the registry as a side effect.
# Ordering doesn't matter -- each @node call is independent.
def _load_all():
    from . import (  # noqa: F401
        triggers,
        ai_nodes,
        logic_nodes,
        frappe_nodes,
        integration_nodes,
        transform_nodes,
    )


_load_all()
