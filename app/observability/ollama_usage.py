"""
app/observability/ollama_usage.py

Token usage for ChatOllama spans in Opik.

Opik's LangChain tracer ships usage extractors for OpenAI, Anthropic, Google,
Vertex, Groq and Bedrock. Ollama is not on the list, so every ChatOllama span
arrives in Opik with model=None and usage=None even though langchain-ollama
puts `usage_metadata` on every AIMessage. This module adds an extractor that
follows the same protocol and registers it at import time.

Verified against opik==2.2.96. If a later Opik version adds Ollama natively,
register() becomes a no-op (it checks before inserting).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from opik import llm_usage
from opik.integrations.langchain.provider_usage_extractors import (
    langchain_run_helpers,
    usage_extractor,
)
from opik.integrations.langchain.provider_usage_extractors.langchain_run_helpers import (
    langchain_usage,
)

LOGGER = logging.getLogger(__name__)
PROVIDER = "ollama"
OLLAMA_USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens"}


class OllamaUsageExtractor:
    """Implements ProviderUsageExtractorProtocol for langchain-ollama runs."""

    PROVIDER = PROVIDER  # a plain string is accepted where LLMProvider has no member

    def is_provider_run(self, run_dict: Dict[str, Any]) -> bool:
        try:
            metadata = (run_dict.get("extra") or {}).get("metadata") or {}
            if metadata.get("ls_provider") == PROVIDER:
                return True
            serialized = run_dict.get("serialized") or {}
            return "ChatOllama" in (serialized.get("id") or [])
        except Exception:
            return False

    def get_llm_usage_info(self, run_dict: Dict[str, Any]) -> llm_usage.LLMUsageInfo:
        return llm_usage.LLMUsageInfo(
            provider=PROVIDER,
            model=_model_name(run_dict),
            usage=_token_usage(run_dict),
        )


def _model_name(run_dict: Dict[str, Any]) -> Optional[str]:
    metadata = (run_dict.get("extra") or {}).get("metadata")
    if isinstance(metadata, dict) and metadata.get("ls_model_name"):
        return str(metadata["ls_model_name"])
    kwargs = (run_dict.get("serialized") or {}).get("kwargs") or {}
    return kwargs.get("model")


def _token_usage(run_dict: Dict[str, Any]) -> Optional[llm_usage.OpikUsage]:
    try:
        found = langchain_run_helpers.try_to_get_usage_by_search(run_dict, set(OLLAMA_USAGE_KEYS))
        if found is None:
            return None
        if isinstance(found, langchain_usage.LangChainUsage):
            return llm_usage.OpikUsage.from_openai_completions_dict(found.map_to_groq_completions_usage())
        if isinstance(found, dict):
            mapped = {
                "prompt_tokens": found.get("input_tokens", found.get("prompt_tokens")),
                "completion_tokens": found.get("output_tokens", found.get("completion_tokens")),
                "total_tokens": found.get("total_tokens"),
            }
            if mapped["total_tokens"] is None and None not in (mapped["prompt_tokens"], mapped["completion_tokens"]):
                mapped["total_tokens"] = mapped["prompt_tokens"] + mapped["completion_tokens"]
            return llm_usage.OpikUsage.from_openai_completions_dict(mapped)
    except Exception:
        LOGGER.debug("Could not extract Ollama token usage", exc_info=True)
    return None


def register() -> bool:
    """Insert the extractor at the front of Opik's registry. Idempotent."""
    registry = usage_extractor._REGISTERED_PROVIDER_USAGE_EXTRACTORS
    if any(getattr(e, "PROVIDER", None) == PROVIDER for e in registry):
        return False
    registry.insert(0, OllamaUsageExtractor())
    return True


register()
