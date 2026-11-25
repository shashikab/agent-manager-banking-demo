"""Strands agent construction."""

from __future__ import annotations

import boto3
from botocore import UNSIGNED
from botocore.config import Config as BotoConfig
from strands import Agent
from strands.models import BedrockModel

from config import Config
from system_prompt import SYSTEM_PROMPT
from tools import ALL_TOOLS

# Low temperature: a banking assistant should be consistent, not creative.
_TEMPERATURE = 0.2
_MAX_TOKENS = 800


def _direct_model(cfg: Config) -> BedrockModel:
    """Call Amazon Bedrock directly with a Bedrock API key (Bearer token auth:
    Strands sends the key as "Authorization: Bearer ..." instead of signing with
    IAM credentials). Agent Manager guardrails cannot see this traffic."""
    return BedrockModel(
        api_key=cfg.bedrock_api_key,
        region_name=cfg.aws_region,
        model_id=cfg.bedrock_model_id,
        # The HTTP endpoint returns one final reply, so there is nothing to stream.
        streaming=False,
        temperature=_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
    )


def _gateway_model(cfg: Config) -> BedrockModel:
    """Call Bedrock through the Agent Manager AI gateway (an LLM provider), which
    is what lets Agent Manager apply guardrails to every request and response.

    The gateway serves the native Bedrock Converse route under its base URL and
    authenticates the agent with its own key in an `api-key` header; the real
    Bedrock key stays in the provider. Requests are unsigned, so the agent needs
    no AWS credentials. This follows WSO2's Bedrock gateway sample."""
    model = BedrockModel(
        boto_session=boto3.Session(
            aws_access_key_id="unused",
            aws_secret_access_key="unused",
            region_name=cfg.aws_region,
        ),
        boto_client_config=BotoConfig(
            signature_version=UNSIGNED,
            connect_timeout=10,
            read_timeout=60,
            retries={"total_max_attempts": 2, "mode": "standard"},
        ),
        endpoint_url=cfg.llm_provider_url,
        model_id=cfg.bedrock_model_id,
        streaming=False,
        temperature=_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
    )

    def authenticate_gateway(request, **kwargs):
        # Never forward ambient AWS credentials (for example a leftover
        # AWS_BEARER_TOKEN_BEDROCK) to the gateway: it gets only its own key.
        for header in ("Authorization", "X-Amz-Security-Token"):
            if header in request.headers:
                del request.headers[header]
        request.headers["api-key"] = cfg.llm_provider_key

    model.client.meta.events.register("before-send.bedrock-runtime.*", authenticate_gateway)
    return model


def build_model(cfg: Config) -> BedrockModel:
    return _gateway_model(cfg) if cfg.use_llm_provider else _direct_model(cfg)


def build_agent(cfg: Config) -> Agent:
    """One agent per conversation: Strands keeps the message history on the
    Agent instance, which is what lets "transfer $200" follow "yes, confirm"."""
    return Agent(
        model=build_model(cfg),
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT.format(bank=cfg.bank_name),
        # No streaming to stdout; the HTTP response carries the final text.
        callback_handler=None,
    )
