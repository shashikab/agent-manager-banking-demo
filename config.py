"""Instance configuration, read from environment variables at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name) or default
    if val is None:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _bool_env(name: str) -> bool:
    raw = _env(name, "false").strip().lower()
    if raw not in {"true", "false"}:
        raise RuntimeError(f"{name} must be true or false, got: {raw!r}")
    return raw == "true"


def _provider_url(value: str) -> str:
    """Validate the gateway URL Agent Manager injects. Plain http is allowed: the
    gateway is usually reached in-cluster, where the URL is http."""
    parsed = urlparse(value)
    try:
        parsed.port
    except ValueError:
        raise RuntimeError("LLM_PROVIDER_URL has an invalid port") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "LLM_PROVIDER_URL must be an http(s) URL without credentials, query or fragment"
        )
    return value.rstrip("/")


@dataclass(frozen=True)
class Config:
    use_llm_provider: bool
    llm_provider_url: str
    llm_provider_key: str
    bedrock_api_key: str
    bedrock_model_id: str
    aws_region: str
    bank_name: str
    agent_version: str

    @property
    def llm_route(self) -> str:
        """Where model calls go. Only the gateway route can be governed by Agent
        Manager guardrails."""
        return "agent-manager-gateway" if self.use_llm_provider else "direct-bedrock"

    @classmethod
    def from_env(cls) -> "Config":
        use_provider = _bool_env("USE_LLM_PROVIDER")
        provider_url = provider_key = bedrock_key = ""
        if use_provider:
            # Gateway mode: Agent Manager injects these two (read-only) variables
            # when an LLM provider is attached to the agent. The agent holds only a
            # gateway key; the real Bedrock key lives in the provider.
            provider_url = os.environ.get("LLM_PROVIDER_URL", "")
            provider_key = os.environ.get("LLM_PROVIDER_KEY", "")
            if not provider_url or not provider_key:
                raise RuntimeError(
                    "USE_LLM_PROVIDER is true but LLM_PROVIDER_URL and/or LLM_PROVIDER_KEY "
                    "is not set. Attach an LLM provider under Configure > LLM Configurations "
                    "and name its two variables LLM_PROVIDER_URL and LLM_PROVIDER_KEY."
                )
            provider_url = _provider_url(provider_url)
        else:
            # Direct mode: an Amazon Bedrock API key (Secret in Agent Manager).
            bedrock_key = _env("AWS_BEARER_TOKEN_BEDROCK")
        return cls(
            use_llm_provider=use_provider,
            llm_provider_url=provider_url,
            llm_provider_key=provider_key,
            bedrock_api_key=bedrock_key,
            # A cross-region inference profile ID (the base model ID is not accepted
            # for on-demand calls). Must be invokable by the Bedrock key in use.
            bedrock_model_id=_env("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6"),
            aws_region=_env("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"),
            bank_name=_env("BANK_NAME", "Demo Bank"),
            # Echoed in /health and every chat response, so a promotion or a
            # rollback is visible from the outside.
            agent_version=_env("AGENT_VERSION", "dev"),
        )
