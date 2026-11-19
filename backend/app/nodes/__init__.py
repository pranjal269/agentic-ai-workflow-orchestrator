"""Node catalog: the typed grammar the planner compiles to.

Each node type is described once, here, as a :class:`~app.nodes.spec.NodeSpec`.
The validator enforces those specs and the executor reads them, so params and
output shapes have a single source of truth.
"""

from app.nodes.catalog import (
    NODE_CATALOG,
    catalog_digest,
    get_node_spec,
    list_specs,
)
from app.nodes.spec import NodeParam, NodeSpec, OutputField

__all__ = [
    "NODE_CATALOG",
    "NodeParam",
    "NodeSpec",
    "OutputField",
    "catalog_digest",
    "get_node_spec",
    "list_specs",
]
