#!/bin/bash
set -e

# Wire OpenRouter key into Anthropic SDK env vars.
# The SDK appends /v1/messages to the base URL, so we strip the trailing /v1.
export ANTHROPIC_API_KEY="${OPENROUTER_API_KEY}"
export ANTHROPIC_BASE_URL="https://openrouter.ai/api"

exec python3 "${AGENT_SCRIPT:-/agents/worker.py}" "$@"
