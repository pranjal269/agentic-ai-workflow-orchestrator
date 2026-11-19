"""Typed specifications for workflow node types.

A :class:`NodeSpec` declares everything the rest of the system needs to know
about a node type without importing its implementation:

* ``params``         - the configurable inputs, with types and constraints;
* ``outputs``        - the fields the node writes, so data bindings can be
                       checked statically;
* ``output_handles`` - the named branches control flow can leave from
                       (``["main"]`` for a plain step, ``["true", "false"]``
                       for a conditional, and so on).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NodeCategory = Literal["trigger", "action", "logic"]
ParamType = Literal["string", "number", "boolean", "object", "array"]


class NodeParam(BaseModel):
    """One configurable input of a node type."""

    name: str
    type: ParamType
    required: bool = False
    description: str = ""
    default: Any = None
    enum: list[Any] | None = None


class OutputField(BaseModel):
    """One field a node writes to its output document."""

    name: str
    type: ParamType
    description: str = ""


class NodeSpec(BaseModel):
    """The complete description of a node type."""

    type: str
    category: NodeCategory
    description: str
    params: list[NodeParam] = Field(default_factory=list)
    outputs: list[OutputField] = Field(default_factory=list)
    output_handles: list[str] = Field(default_factory=lambda: ["main"])
    credential_type: str | None = None

    def param(self, name: str) -> NodeParam | None:
        """Return the named param spec, or ``None`` if this node has no such param."""

        return next((p for p in self.params if p.name == name), None)

    def output_names(self) -> set[str]:
        """Return the set of declared output field names."""

        return {field.name for field in self.outputs}
