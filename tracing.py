"""OpenTelemetry settings that make Agent Manager's Traces view show what the agent
was asked and what it did."""

from __future__ import annotations

import os

_VAR = "OTEL_SEMCONV_STABILITY_OPT_IN"

# gen_ai_latest_experimental: records tool input and output on tool spans.
# gen_ai_span_attributes_only: records prompt and reply messages as span ATTRIBUTES
#   (gen_ai.input.messages / gen_ai.output.messages). By default Strands writes them
#   as span EVENTS, which Agent Manager's trace viewer ignores, so the Input and
#   Output columns stay empty.
REQUIRED = ("gen_ai_latest_experimental", "gen_ai_span_attributes_only")


def configure() -> None:
    """Add the required tokens to OTEL_SEMCONV_STABILITY_OPT_IN, keeping any others.

    Must run before `strands` is imported: Strands reads the setting when its
    tracer is first created."""
    tokens = [t.strip() for t in os.environ.get(_VAR, "").split(",") if t.strip()]
    tokens += [t for t in REQUIRED if t not in tokens]
    os.environ[_VAR] = ",".join(tokens)
