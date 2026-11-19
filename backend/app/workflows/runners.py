"""Per-node behaviour for the execution engine.

Each node type has a runner that takes the node, its evaluation context (every
upstream node's output plus this node's resolved bindings) and returns a
:class:`NodeResult`: the output document the node writes, and which of its
output handles control flow should leave from.

The two runners that reach the outside world - ``http_request`` and
``llm_step`` - go through injected callers so the engine stays testable and
runs offline by default:

* :class:`HttpCaller` - defaults to a real ``httpx`` client.
* :class:`LlmCaller`  - defaults to an OpenAI-compatible chat endpoint (NVIDIA's
  hosted API) when a key is configured, otherwise a deterministic stub
  (mirroring the planner).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import LLM_BACKEND_NAME, Settings, settings as default_settings
from app.core.llm_client import build_chat_client, thinking_extra_body
from app.nodes.spec import NodeSpec
from app.schemas.workflow import WorkflowNode
from app.workflows.expressions import ExpressionError, evaluate, render, render_object, truthy


class NodeRunError(RuntimeError):
    """A node failed at run time (bad expression, transport error, ...)."""


@dataclass
class NodeResult:
    """What a node produced: its output document and its active handles."""

    output: dict[str, Any]
    handles: list[str] = field(default_factory=lambda: ["main"])


# --------------------------------------------------------------------------- #
# HTTP caller
# --------------------------------------------------------------------------- #


@dataclass
class HttpReply:
    """The parts of an HTTP response the engine exposes to downstream nodes."""

    status_code: int
    headers: dict[str, str]
    body: Any


class HttpCaller(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, Any] | None,
        params: dict[str, Any] | None,
        json_body: Any | None,
    ) -> HttpReply: ...


class HttpxCaller:
    """Default :class:`HttpCaller`, backed by a lazily-created ``httpx`` client."""

    def __init__(self, timeout: float = 15.0, max_bytes: int = 1_000_000) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=self._timeout, follow_redirects=True)
        return self._client

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, Any] | None,
        params: dict[str, Any] | None,
        json_body: Any | None,
    ) -> HttpReply:
        import httpx

        try:
            response = self._get_client().request(
                method.upper(),
                url,
                headers=headers or None,
                params=params or None,
                json=json_body if json_body not in (None, {}) else None,
            )
        except httpx.HTTPError as exc:
            raise NodeRunError(f"HTTP request to {url!r} failed: {exc}") from exc

        return HttpReply(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=_parse_http_body(response, self._max_bytes),
        )


def _parse_http_body(response: Any, max_bytes: int) -> Any:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            pass
    text = response.text
    if len(text) > max_bytes:
        return text[:max_bytes] + f"\n...[truncated {len(text) - max_bytes} chars]"
    return text


# --------------------------------------------------------------------------- #
# LLM caller
# --------------------------------------------------------------------------- #


class LlmCaller(Protocol):
    def __call__(
        self, prompt: str, *, system: str | None, model: str, temperature: float
    ) -> str: ...


class StubLlmCaller:
    """Deterministic, offline stand-in used when no NVIDIA key is configured."""

    def __call__(
        self, prompt: str, *, system: str | None, model: str, temperature: float
    ) -> str:
        snippet = prompt.strip().replace("\n", " ")
        if len(snippet) > 280:
            snippet = snippet[:280] + "..."
        return f"[offline-llm:{model}] {snippet}"


class NvidiaLlmCaller:
    """Calls an OpenAI-compatible chat endpoint (NVIDIA) for ``llm_step`` nodes."""

    _MAX_TOKENS = 2048

    def __init__(self, settings: Settings) -> None:
        self._client = build_chat_client(settings)
        self._default_model = settings.EXECUTOR_LLM_MODEL
        self._extra_body = thinking_extra_body(settings)

    def __call__(
        self, prompt: str, *, system: str | None, model: str, temperature: float
    ) -> str:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "max_tokens": self._MAX_TOKENS,
            "temperature": temperature if temperature is not None else 0,
        }
        if self._extra_body is not None:
            kwargs["extra_body"] = self._extra_body

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any SDK/transport failure uniformly
            raise NodeRunError(f"llm_step call failed: {exc}") from exc

        return response.choices[0].message.content or ""


def resolve_llm_caller(settings: Settings | None = None) -> LlmCaller:
    """Pick an LLM caller from settings: the real LLM when a key is present, else stub."""

    settings = settings or default_settings
    if settings.executor_llm_backend_resolved == LLM_BACKEND_NAME:
        return NvidiaLlmCaller(settings)
    return StubLlmCaller()


# --------------------------------------------------------------------------- #
# node runners
# --------------------------------------------------------------------------- #


def run_node(
    node: WorkflowNode,
    spec: NodeSpec,
    context: dict[str, Any],
    iteration: int,
    *,
    http: HttpCaller,
    llm: LlmCaller,
) -> NodeResult:
    """Execute one node and return its output plus its active output handles."""

    runner = _RUNNERS.get(node.type, _run_passthrough)
    try:
        return runner(node, spec, context, iteration, http, llm)
    except (ExpressionError, NodeRunError):
        raise
    except Exception as exc:  # noqa: BLE001 - any runner failure is a node failure
        raise NodeRunError(f"{node.type} node {node.id!r} failed: {exc}") from exc


def _run_http_request(
    node: WorkflowNode,
    spec: NodeSpec,
    context: dict[str, Any],
    iteration: int,
    http: HttpCaller,
    llm: LlmCaller,
) -> NodeResult:
    params = node.params
    url = render(params.get("url"), context)
    if not isinstance(url, str) or not url:
        raise NodeRunError(f"http_request node {node.id!r} has no usable url")

    method = str(params.get("method", "GET"))
    headers = _string_map(render_object(params.get("headers") or {}, context))
    query = _string_map(render_object(params.get("query") or {}, context))
    body = render_object(params.get("body"), context) if params.get("body") is not None else None

    reply = http(method, url, headers=headers, params=query, json_body=body)
    return NodeResult(
        output={
            "statusCode": reply.status_code,
            "headers": reply.headers,
            "body": reply.body,
        }
    )


def _run_llm_step(
    node: WorkflowNode,
    spec: NodeSpec,
    context: dict[str, Any],
    iteration: int,
    http: HttpCaller,
    llm: LlmCaller,
) -> NodeResult:
    params = node.params
    prompt = render(params.get("prompt"), context)
    if not isinstance(prompt, str) or not prompt.strip():
        raise NodeRunError(f"llm_step node {node.id!r} has an empty prompt")

    system = params.get("system")
    system_text = render(system, context) if isinstance(system, str) else None
    model = str(params.get("model") or default_settings.EXECUTOR_LLM_MODEL)
    temperature = params.get("temperature", 0)

    text = llm(prompt, system=system_text or None, model=model, temperature=temperature)
    return NodeResult(output={"text": text})


def _run_set(
    node: WorkflowNode,
    spec: NodeSpec,
    context: dict[str, Any],
    iteration: int,
    http: HttpCaller,
    llm: LlmCaller,
) -> NodeResult:
    values = node.params.get("values") or {}
    if not isinstance(values, dict):
        raise NodeRunError(f"set node {node.id!r} expects 'values' to be an object")
    return NodeResult(output={"values": render_object(values, context)})


def _run_if(
    node: WorkflowNode,
    spec: NodeSpec,
    context: dict[str, Any],
    iteration: int,
    http: HttpCaller,
    llm: LlmCaller,
) -> NodeResult:
    condition = node.params.get("condition")
    if not isinstance(condition, str) or not condition.strip():
        raise NodeRunError(f"if node {node.id!r} has no condition")
    result = truthy(render(condition, context))
    return NodeResult(output={"result": result}, handles=["true" if result else "false"])


def _run_loop(
    node: WorkflowNode,
    spec: NodeSpec,
    context: dict[str, Any],
    iteration: int,
    http: HttpCaller,
    llm: LlmCaller,
) -> NodeResult:
    params = node.params
    index = iteration - 1  # iteration is 1-based

    raw_items = params.get("items")
    items = evaluate(raw_items, context) if isinstance(raw_items, str) else raw_items

    if isinstance(items, list):
        if index < len(items):
            return NodeResult(output={"item": items[index], "index": index}, handles=["each"])
        return NodeResult(output={"item": None, "index": index}, handles=["done"])

    condition = params.get("condition")
    if isinstance(condition, str) and condition.strip():
        if truthy(render(condition, context)):
            return NodeResult(output={"item": None, "index": index}, handles=["each"])
        return NodeResult(output={"item": None, "index": index}, handles=["done"])

    return NodeResult(output={"item": None, "index": index}, handles=["done"])


def _run_passthrough(
    node: WorkflowNode,
    spec: NodeSpec,
    context: dict[str, Any],
    iteration: int,
    http: HttpCaller,
    llm: LlmCaller,
) -> NodeResult:
    handles = list(spec.output_handles) if spec.output_handles else ["main"]
    return NodeResult(output={"type": node.type, "iteration": iteration}, handles=handles)


_RUNNERS = {
    "http_request": _run_http_request,
    "llm_step": _run_llm_step,
    "set": _run_set,
    "if": _run_if,
    "loop": _run_loop,
}


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): "" if item is None else str(item) for key, item in value.items()}
