"""Shared OpenAI-compatible client used by the planner and ``llm_step`` nodes.

Both call sites talk to the same endpoint (NVIDIA's hosted API by default, any
OpenAI-compatible server via ``LLM_BASE_URL``). This module owns constructing
the client and the one provider-specific detail we pass through - NVIDIA's
``chat_template_kwargs.enable_thinking`` toggle, sent inside ``extra_body``.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings, settings as default_settings


class LlmClientError(RuntimeError):
    """Raised when the OpenAI client cannot be constructed."""


def build_chat_client(settings: Settings | None = None) -> Any:
    """Return an ``openai.OpenAI`` client pointed at the configured endpoint."""

    settings = settings or default_settings
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on env
        raise LlmClientError(
            "The 'openai' package is required for the real LLM backend. "
            "Install it (pip install -r requirements.txt) or use *_BACKEND=stub."
        ) from exc

    # A custom base_url always needs an explicit key; the client only errors on
    # the first request, so a placeholder keeps construction (and tests) safe.
    return OpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.NVIDIA_API_KEY or "MISSING_NVIDIA_API_KEY",
    )


def thinking_extra_body(settings: Settings | None = None) -> dict[str, Any] | None:
    """``extra_body`` for a chat completion, or ``None`` when thinking is off."""

    settings = settings or default_settings
    if settings.LLM_ENABLE_THINKING:
        return {"chat_template_kwargs": {"enable_thinking": True}}
    return None
