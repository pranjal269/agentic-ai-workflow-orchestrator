"""Application configuration, loaded from environment variables.

The LLM API key only ever comes from the environment - never commit it. The
planner and ``llm_step`` nodes call an OpenAI-compatible endpoint; the defaults
below target NVIDIA's hosted API (``nvidia/nemotron-3-super-120b-a12b`` on
``https://integrate.api.nvidia.com/v1``). Point ``LLM_BASE_URL`` and the model
names elsewhere for any other OpenAI-compatible provider.

    NVIDIA_API_KEY=nvapi-...            # enables the real LLM planner + llm_step nodes
    LLM_BASE_URL=https://integrate.api.nvidia.com/v1
    LLM_ENABLE_THINKING=false           # pass chat_template_kwargs.enable_thinking
    PLANNER_BACKEND=auto                # auto | stub | nvidia
    PLANNER_MODEL=nvidia/nemotron-3-super-120b-a12b
    PLANNER_MAX_REPAIR_ATTEMPTS=2       # validation-driven re-plan attempts
    EXECUTOR_LLM_BACKEND=auto           # auto | stub | nvidia (llm_step nodes)
    EXECUTOR_LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
    EXECUTOR_HTTP_TIMEOUT=15            # seconds; per http_request node
    EXECUTOR_MAX_RESPONSE_BYTES=1000000 # truncate oversized HTTP response bodies
    CORS_ORIGINS=http://localhost:5173  # comma-separated
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

# Load backend/.env if present. Real environment variables always win
# (load_dotenv defaults to override=False), and the import is optional so the
# app still runs if python-dotenv is not installed.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # pragma: no cover - dotenv is a convenience, not a requirement
    pass

_DEFAULT_CORS = ["http://localhost:5173", "http://127.0.0.1:5173"]
_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"

# Resolved value of a "*_BACKEND" setting when a real LLM is available.
LLM_BACKEND_NAME = "nvidia"


class Settings(BaseModel):
    """Resolved application settings."""

    PROJECT_NAME: str = "Agentic AI Workflow Orchestrator"
    VERSION: str = "0.2.0"
    CORS_ORIGINS: list[str] = _DEFAULT_CORS

    # LLM transport (OpenAI-compatible; NVIDIA by default)
    NVIDIA_API_KEY: str | None = None
    LLM_BASE_URL: str = _DEFAULT_BASE_URL
    LLM_ENABLE_THINKING: bool = False

    # Planner
    PLANNER_BACKEND: str = "auto"  # auto -> nvidia if a key is present, else stub
    PLANNER_MODEL: str = _DEFAULT_MODEL
    PLANNER_MAX_REPAIR_ATTEMPTS: int = 2

    # Execution engine
    EXECUTOR_LLM_BACKEND: str = "auto"  # auto -> nvidia if a key is present, else stub
    EXECUTOR_LLM_MODEL: str = _DEFAULT_MODEL
    EXECUTOR_HTTP_TIMEOUT: float = 15.0
    EXECUTOR_MAX_RESPONSE_BYTES: int = 1_000_000

    @property
    def planner_backend_resolved(self) -> str:
        """"auto" collapses to the real backend when an API key is configured."""

        if self.PLANNER_BACKEND != "auto":
            return self.PLANNER_BACKEND
        return LLM_BACKEND_NAME if self.NVIDIA_API_KEY else "stub"

    @property
    def executor_llm_backend_resolved(self) -> str:
        """"auto" collapses to the real backend when an API key is configured."""

        if self.EXECUTOR_LLM_BACKEND != "auto":
            return self.EXECUTOR_LLM_BACKEND
        return LLM_BACKEND_NAME if self.NVIDIA_API_KEY else "stub"


def _split_csv(value: str | None, fallback: list[str]) -> list[str]:
    if not value:
        return fallback
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    """Build :class:`Settings` from the current environment."""

    return Settings(
        CORS_ORIGINS=_split_csv(os.getenv("CORS_ORIGINS"), _DEFAULT_CORS),
        NVIDIA_API_KEY=os.getenv("NVIDIA_API_KEY") or None,
        LLM_BASE_URL=os.getenv("LLM_BASE_URL", _DEFAULT_BASE_URL),
        LLM_ENABLE_THINKING=_env_bool("LLM_ENABLE_THINKING", False),
        PLANNER_BACKEND=os.getenv("PLANNER_BACKEND", "auto"),
        PLANNER_MODEL=os.getenv("PLANNER_MODEL", _DEFAULT_MODEL),
        PLANNER_MAX_REPAIR_ATTEMPTS=int(os.getenv("PLANNER_MAX_REPAIR_ATTEMPTS", "2")),
        EXECUTOR_LLM_BACKEND=os.getenv("EXECUTOR_LLM_BACKEND", "auto"),
        EXECUTOR_LLM_MODEL=os.getenv("EXECUTOR_LLM_MODEL", _DEFAULT_MODEL),
        EXECUTOR_HTTP_TIMEOUT=float(os.getenv("EXECUTOR_HTTP_TIMEOUT", "15")),
        EXECUTOR_MAX_RESPONSE_BYTES=int(os.getenv("EXECUTOR_MAX_RESPONSE_BYTES", "1000000")),
    )


settings = load_settings()
