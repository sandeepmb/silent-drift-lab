"""
app/observability/otel_setup.py

The OpenTelemetry track. Everything the native track does with OpikTracer,
done with vendor-neutral OpenTelemetry spans instead:

  LangChain / LangGraph run  →  OTelLangChainHandler  →  OTel spans
      →  BatchSpanProcessor  →  OTLP/HTTP exporter
          →  OpenTelemetry Collector (:4318)  →  Opik            (default)
          →  Opik directly, /api/v1/private/otel/v1/traces       (OTEL_DIRECT_TO_OPIK=true)

Public surface (used by opik_setup.py, a2a_client.py, quiz_service.py, scripts/):

  configure_otel()                      one TracerProvider per process, idempotent
  get_otel_callbacks(session_id, tags)  → [OTelLangChainHandler]  (what opik_setup wires in)
  flush_otel()                          force_flush before exit
  inject_trace_headers(headers)         add W3C `traceparent` for an outgoing HTTP call
  server_span(name, headers, ...)       continue an incoming trace on the server side
  OTelLangChainHandler                  the callback handler itself (also used standalone)

Attribute vocabulary (what Opik 2.2.96 maps, see docs/otel-mapping.md):

  LLM spans   gen_ai.operation.name="chat", gen_ai.system / gen_ai.provider.name,
              gen_ai.request.model, gen_ai.request.temperature, gen_ai.input.messages,
              gen_ai.output.messages, gen_ai.response.model,
              gen_ai.response.finish_reasons, gen_ai.usage.input_tokens,
              gen_ai.usage.output_tokens, server.address
  tool spans  gen_ai.operation.name="execute_tool", gen_ai.tool.name, gen_ai.tool.type,
              gen_ai.tool.call.arguments, gen_ai.tool.call.result
  chain/node  input, output (JSON strings), opik.metadata.langgraph_*
  root span   + gen_ai.operation.name="invoke_agent", gen_ai.agent.name, gen_ai.framework,
              gen_ai.conversation.id, thread_id, opik.tags, opik.metadata.*

Design notes
  * Parent/child comes from LangChain's run_id / parent_run_id, not from the
    OTel context. The context is attached too (so `inject()` and any other OTel
    instrumentation see the right parent), but it is never authoritative.
  * Only attributes Opik has a rule for are emitted. Opik buckets any UNMAPPED
    span attribute into `input`, so a stray `foo.bar` key would pollute the
    span input. Custom values go under `opik.metadata.<key>`.
  * In TELEMETRY_MODE=otel the native `@track` decorators in app/agents are
    switched off at runtime (opik.set_tracing_active(False)) so the OTel track
    is OTel-only. Set OTEL_KEEP_NATIVE_TRACK=true to keep them; then the
    OpikSpanProcessor registered below links OTel spans that start inside a
    `@track` function into that native trace (see its docstring for the caveat).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import threading
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlparse
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

# ── Configuration (env, same names as .env) ─────────────────────────────────

PROJECT = os.getenv("OPIK_PROJECT_NAME", "silent-drift-lab")
WORKSPACE = os.getenv("OPIK_WORKSPACE", "default")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# The instrumentation scope name. Opik surfaces it as metadata.integration on every span.
INSTRUMENTATION_SCOPE = "silent-drift-lab.otel-track"

# JSON attributes larger than this are truncated (Opik then stores them as text, not JSON).
MAX_ATTR_CHARS = int(os.getenv("OTEL_ATTR_MAX_CHARS", "200000"))

LANGGRAPH_HIDDEN_TAG = "langsmith:hidden"   # LangGraph's internal ChannelWrite / __start__ runs


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ── TracerProvider, configured once per process ─────────────────────────────

_provider: Optional[TracerProvider] = None
_provider_lock = threading.Lock()
_export_target = "(not configured)"


def _build_exporter() -> tuple[OTLPSpanExporter, str]:
    """Collector by default; straight to Opik when OTEL_DIRECT_TO_OPIK=true."""
    if _env_bool("OTEL_DIRECT_TO_OPIK"):
        opik_api = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api").rstrip("/")
        endpoint = f"{opik_api}/v1/private/otel/v1/traces"
        headers = {"Comet-Workspace": WORKSPACE, "projectName": PROJECT}
        if os.getenv("OPIK_API_KEY"):                       # Comet cloud only
            headers["Authorization"] = os.environ["OPIK_API_KEY"]
        return OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=30), f"Opik direct {endpoint}"

    base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318").rstrip("/")
    endpoint = base if base.endswith("/v1/traces") else f"{base}/v1/traces"
    return OTLPSpanExporter(endpoint=endpoint, timeout=30), f"Collector {endpoint}"


class ScopeFilteringExporter(SpanExporter):
    """
    Drop spans from unwanted instrumentation scopes before export.

    Once a real TracerProvider is installed, every library that calls
    `trace.get_tracer(...)` starts emitting. a2a-sdk (scope `a2a-python-sdk`)
    traces its event-queue polling loop: a new root span every ~0.5 s, which Opik
    would turn into a new trace each. Filter those out; keep everything else.
    """

    def __init__(self, delegate: SpanExporter, drop_scope_prefixes: list[str]) -> None:
        self._delegate = delegate
        self._drop = tuple(drop_scope_prefixes)

    def _keep(self, span: ReadableSpan) -> bool:
        scope = getattr(span, "instrumentation_scope", None)
        name = getattr(scope, "name", "") or ""
        return not name.startswith(self._drop)

    def export(self, spans):  # type: ignore[override]
        kept = [s for s in spans if self._keep(s)]
        if not kept:
            return SpanExportResult.SUCCESS
        return self._delegate.export(kept)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def configure_otel(opik_span_processor: Optional[bool] = None) -> TracerProvider:
    """
    Create the process-wide TracerProvider (idempotent: the first call wins).

      resource.service.name     ← OTEL_SERVICE_NAME (default learning-accelerator)
      BatchSpanProcessor        → ScopeFilteringExporter → OTLP/HTTP (Collector or Opik direct)
                                  drops scopes in OTEL_DROP_SCOPE_PREFIXES (default a2a-python-sdk)
      OpikSpanProcessor         `opik_span_processor` arg, else OTEL_OPIK_SPAN_PROCESSOR
                                  (default true in the app, false in server_span()), see below
      ConsoleSpanExporter       when OTEL_CONSOLE_EXPORT=true (debugging)

    What OpikSpanProcessor does (opik/integrations/otel/processor.py, SDK 2.2.96):
    on every span *start* it looks for Opik ids to inherit, in this order:
      1. the parent OTel span's `opik.trace_id` + `opik.span_id` attributes;
      2. OTel baggage keys `opik.trace_id` / `opik.span_id` (cross-process);
      3. the active native `@track` span (opik_context.get_distributed_trace_headers()).
    If it finds them it stamps `opik.trace_id`, a freshly minted UUIDv7 `opik.span_id`,
    and `opik.parent_span_id` on the new span. The backend's OpenTelemetryMapper
    then uses those ids verbatim, so the OTel span lands *inside* the native trace
    instead of in a synthetic OTel-only trace. Spans with no Opik signal anywhere
    are left untouched (the normal SHA-256-of-otel-id path applies).
    In this app it only matters when native `@track` spans are alive at the same
    time as OTel spans, i.e. OTEL_KEEP_NATIVE_TRACK=true or scripts/otel_probe.py
    --nest-under-native. In the default otel mode it is a no-op.
    """
    global _provider, _export_target
    with _provider_lock:
        if _provider is not None:
            return _provider

        service_name = os.getenv("OTEL_SERVICE_NAME", "learning-accelerator")
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.namespace": "silent-drift-lab",
                "deployment.environment.name": os.getenv("OTEL_DEPLOYMENT_ENV", "local"),
            }
        )
        provider = TracerProvider(resource=resource)

        exporter, target = _build_exporter()
        drop_scopes = [p.strip() for p in os.getenv("OTEL_DROP_SCOPE_PREFIXES", "a2a-python-sdk").split(",") if p.strip()]
        if drop_scopes:
            exporter = ScopeFilteringExporter(exporter, drop_scopes)
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                schedule_delay_millis=int(os.getenv("OTEL_BSP_SCHEDULE_DELAY", "2000")),
                max_export_batch_size=256,
            )
        )
        if _env_bool("OTEL_CONSOLE_EXPORT"):
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

        register_opik = (
            opik_span_processor
            if opik_span_processor is not None
            else _env_bool("OTEL_OPIK_SPAN_PROCESSOR", default=True)
        )
        if register_opik:
            try:
                from opik.integrations.otel import OpikSpanProcessor

                provider.add_span_processor(OpikSpanProcessor())
            except Exception as e:  # opik not installed / API moved: the track still works
                print(f"[OTel] OpikSpanProcessor not registered: {e}")

        try:
            trace.set_tracer_provider(provider)   # warns (does not raise) if one is already set
        except Exception:
            pass

        _provider = provider
        _export_target = target
        print(f"[OTel] service.name={service_name} → {target}")
        return provider


def get_tracer() -> trace.Tracer:
    return configure_otel().get_tracer(INSTRUMENTATION_SCOPE)


def flush_otel(timeout_ms: int = 15000) -> None:
    """Flush pending spans. Called by opik_setup.flush() at the end of a session."""
    if _provider is not None:
        _provider.force_flush(timeout_millis=timeout_ms)


def export_target() -> str:
    return _export_target


# ── JSON helpers ────────────────────────────────────────────────────────────

def _jsonable(obj: Any, depth: int = 0) -> Any:
    """Best-effort conversion of LangChain/LangGraph objects to JSON-serialisable data."""
    if depth > 12:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in obj]
    # LangChain messages → compact dicts (type, content, tool_calls)
    msg_type = getattr(obj, "type", None)
    if msg_type in ("human", "ai", "system", "tool", "function") and hasattr(obj, "content"):
        d: dict[str, Any] = {"type": msg_type, "content": _jsonable(obj.content, depth + 1)}
        tool_calls = getattr(obj, "tool_calls", None)
        if tool_calls:
            d["tool_calls"] = _jsonable(tool_calls, depth + 1)
        tool_call_id = getattr(obj, "tool_call_id", None)
        if tool_call_id:
            d["tool_call_id"] = tool_call_id
        return d
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        try:
            return _jsonable(dataclasses.asdict(obj), depth + 1)
        except Exception:
            return str(obj)
    if hasattr(obj, "model_dump"):
        try:
            return _jsonable(obj.model_dump(), depth + 1)
        except Exception:
            return str(obj)
    return str(obj)


def _json(obj: Any) -> str:
    s = json.dumps(_jsonable(obj), ensure_ascii=False, default=str)
    if len(s) > MAX_ATTR_CHARS:
        s = s[:MAX_ATTR_CHARS] + "...[truncated]"
    return s


def _json_object(obj: Any) -> str:
    """
    JSON for the `input` / `output` attributes, always an object.

    Opik merges a JSON *object* string into the span's input/output fields. A bare
    scalar (a routing function returning "end") would be stored as text after a
    backend warning, so non-dict values are wrapped as {"value": ...}.
    """
    return _json(obj if isinstance(obj, dict) else {"value": obj})


# ── GenAI message shapes (OTel GenAI semconv `gen_ai.input.messages`) ────────

_ROLE = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool", "function": "tool"}


def _content_parts(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "content": content}] if content else []
    parts = []
    for block in content if isinstance(content, list) else [content]:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append({"type": "text", "content": block.get("text", "")})
        else:
            parts.append({"type": "text", "content": json.dumps(_jsonable(block), ensure_ascii=False)})
    return parts


def _genai_message(msg: Any) -> dict:
    """LangChain BaseMessage → {"role", "parts": [...]} per the GenAI semconv."""
    msg_type = getattr(msg, "type", "user")
    role = _ROLE.get(msg_type, msg_type)
    if msg_type == "tool":
        return {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_call_response",
                    "id": getattr(msg, "tool_call_id", None),
                    "response": _jsonable(getattr(msg, "content", "")),
                }
            ],
        }
    parts = _content_parts(getattr(msg, "content", ""))
    for tc in getattr(msg, "tool_calls", None) or []:
        parts.append(
            {
                "type": "tool_call",
                "id": tc.get("id"),
                "name": tc.get("name"),
                "arguments": _jsonable(tc.get("args")),
            }
        )
    return {"role": role, "parts": parts}


# ── LangChain configure hook ────────────────────────────────────────────────
# A ContextVar that LangChain consults when it builds a callback manager for
# any Runnable. Setting it makes bare `llm.invoke(...)` calls (no explicit
# callbacks, e.g. inside the quiz service) report to our handler. Inheritable,
# and asyncio.to_thread copies contextvars, so it survives the thread hop.

_HANDLER_VAR: ContextVar[Optional[BaseCallbackHandler]] = ContextVar("otel_langchain_handler", default=None)
_hook_registered = False


def _register_hook_once() -> None:
    global _hook_registered
    if not _hook_registered:
        register_configure_hook(_HANDLER_VAR, inheritable=True)
        _hook_registered = True


@contextlib.contextmanager
def langchain_handler_active(handler: "OTelLangChainHandler") -> Iterator[None]:
    """Make `handler` the ambient LangChain callback for the duration of the block."""
    _register_hook_once()
    token = _HANDLER_VAR.set(handler)
    try:
        yield
    finally:
        _HANDLER_VAR.reset(token)


# ── The callback handler ────────────────────────────────────────────────────

_live_handlers: list["OTelLangChainHandler"] = []     # for inject_trace_headers() fallback
_live_handlers_lock = threading.Lock()


class OTelLangChainHandler(BaseCallbackHandler):
    """
    Turn LangChain / LangGraph runs into OpenTelemetry spans.

      chain run (graph, node, routing fn)  → INTERNAL span named after the run
      chat model run                       → CLIENT span "chat <model>" with gen_ai.* attributes
      tool run                             → INTERNAL span "execute_tool <name>"

    The root run of each graph.invoke() becomes the trace root and carries the
    thread/tags/metadata attributes that Opik lifts onto the Trace object.
    Hidden LangGraph runs (tag langsmith:hidden) are skipped and their children
    are re-parented to the nearest visible ancestor.
    """

    raise_error = False
    run_inline = True      # keep callbacks on the calling thread (contextvars intact)

    def __init__(
        self,
        session_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        agent_name: str = "learning-accelerator",
    ) -> None:
        super().__init__()
        configure_otel()
        self.session_id = session_id
        self.tags = list(tags or [])
        self.metadata = dict(metadata or {})
        self.agent_name = agent_name
        self._tracer = get_tracer()
        self._spans: dict[UUID, Span] = {}
        self._tokens: dict[UUID, object] = {}
        self._parent_of: dict[UUID, Optional[UUID]] = {}   # effective parent (hidden runs collapsed)
        self._open: list[UUID] = []                          # start order of open spans
        self._lock = threading.RLock()
        self.trace_ids: list[str] = []                       # OTel trace ids (hex) of root spans
        with _live_handlers_lock:
            _live_handlers.append(self)

    # ── bookkeeping ─────────────────────────────────────────────────────

    def _effective_parent(self, parent_run_id: Optional[UUID]) -> Optional[UUID]:
        while parent_run_id is not None and parent_run_id not in self._spans:
            if parent_run_id not in self._parent_of:
                return None
            parent_run_id = self._parent_of[parent_run_id]
        return parent_run_id

    def innermost_open_span(self) -> Optional[Span]:
        with self._lock:
            for run_id in reversed(self._open):
                span = self._spans.get(run_id)
                if span is not None and span.is_recording():
                    return span
        return None

    def _root_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": self.agent_name,
            "gen_ai.framework": "langgraph",
        }
        if self.session_id:
            attrs["gen_ai.conversation.id"] = self.session_id   # → Opik thread (GenAI rule)
            attrs["thread_id"] = self.session_id                # → Opik thread (General rule)
        if self.tags:
            attrs["opik.tags"] = list(self.tags)                # array attribute → Opik tags
        for key, value in self.metadata.items():
            if isinstance(value, (str, bool, int, float)):
                attrs[f"opik.metadata.{key}"] = value
            else:
                attrs[f"opik.metadata.{key}"] = _json(value)
        return attrs

    def _start(
        self,
        run_id: UUID,
        parent_run_id: Optional[UUID],
        name: str,
        kind: SpanKind,
        attributes: dict[str, Any],
        tags: Optional[list[str]] = None,
    ) -> Optional[Span]:
        with self._lock:
            if tags and LANGGRAPH_HIDDEN_TAG in tags:
                self._parent_of[run_id] = parent_run_id
                return None

            parent_run = self._effective_parent(parent_run_id)
            parent_span = self._spans.get(parent_run) if parent_run is not None else None

            if parent_span is not None:
                ctx = trace.set_span_in_context(parent_span)
                is_trace_root = False
            else:
                ctx = otel_context.get_current()            # may hold a server span (quiz service)
                is_trace_root = not trace.get_current_span(ctx).get_span_context().is_valid

            if is_trace_root:
                attributes = {**self._root_attributes(), **attributes}

            span = self._tracer.start_span(name, context=ctx, kind=kind, attributes=attributes)
            if is_trace_root:
                self.trace_ids.append(format(span.get_span_context().trace_id, "032x"))

            self._spans[run_id] = span
            self._parent_of[run_id] = parent_run_id
            self._open.append(run_id)
            try:
                self._tokens[run_id] = otel_context.attach(trace.set_span_in_context(span))
            except Exception:
                pass
            return span

    def _end(self, run_id: UUID, attributes: Optional[dict[str, Any]] = None, error: Optional[BaseException] = None) -> None:
        with self._lock:
            span = self._spans.pop(run_id, None)
            token = self._tokens.pop(run_id, None)
            if run_id in self._open:
                self._open.remove(run_id)
            self._parent_of.pop(run_id, None)
        if span is None:
            return
        try:
            for key, value in (attributes or {}).items():
                if value is not None:
                    span.set_attribute(key, value)
            if error is not None:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, f"{type(error).__name__}: {error}"))
            else:
                span.set_status(Status(StatusCode.OK))
        finally:
            span.end()
            if token is not None:
                # Only detach when this thread's current span is ours; a mismatch
                # (callback fired on another thread) is harmless and must not raise.
                try:
                    if trace.get_current_span() is span:
                        otel_context.detach(token)
                except Exception:
                    pass

    # ── chains: graph root, nodes, routing functions ─────────────────────

    @staticmethod
    def _run_name(serialized: Optional[dict], kwargs: dict, fallback: str) -> str:
        if kwargs.get("name"):
            return str(kwargs["name"])
        if serialized:
            if serialized.get("name"):
                return str(serialized["name"])
            ident = serialized.get("id")
            if isinstance(ident, list) and ident:
                return str(ident[-1])
        return fallback

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        name = self._run_name(serialized, kwargs, "chain")
        attrs: dict[str, Any] = {"input": _json_object(inputs)}
        for key in ("langgraph_node", "langgraph_step", "langgraph_triggers", "langgraph_path"):
            value = (metadata or {}).get(key)
            if value is not None:
                attrs[f"opik.metadata.{key}"] = value if isinstance(value, (str, int, float, bool)) else _json(value)
        self._start(run_id, parent_run_id, name, SpanKind.INTERNAL, attrs, tags)

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        self._end(run_id, {"output": _json_object(outputs)})

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._end(run_id, error=error)

    # ── LLMs ───────────────────────────────────────────────────────────

    def _llm_attributes(self, serialized, metadata, kwargs) -> tuple[str, dict[str, Any]]:
        metadata = metadata or {}
        params = kwargs.get("invocation_params") or {}
        ser_kwargs = (serialized or {}).get("kwargs", {}) if serialized else {}

        model = metadata.get("ls_model_name") or params.get("model") or ser_kwargs.get("model") or MODEL
        provider = metadata.get("ls_provider")
        if not provider:
            ident = (serialized or {}).get("id") or []
            provider = "ollama" if any("ollama" in str(p).lower() for p in ident) else "unknown"
        temperature = metadata.get("ls_temperature", params.get("temperature", ser_kwargs.get("temperature")))

        attrs: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": provider,            # deprecated semconv key; authoritative for Opik 2.2.96
            "gen_ai.provider.name": provider,     # current semconv key
            "gen_ai.request.model": str(model),
        }
        if isinstance(temperature, (int, float)):
            attrs["gen_ai.request.temperature"] = float(temperature)
        base_url = params.get("base_url") or ser_kwargs.get("base_url") or OLLAMA_BASE_URL
        host = urlparse(str(base_url)).hostname
        if host:
            attrs["server.address"] = host
        return str(model), attrs

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        model, attrs = self._llm_attributes(serialized, metadata, kwargs)
        batch = messages[0] if messages else []
        attrs["gen_ai.input.messages"] = _json([_genai_message(m) for m in batch])
        self._start(run_id, parent_run_id, f"chat {model}", SpanKind.CLIENT, attrs, tags)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        model, attrs = self._llm_attributes(serialized, metadata, kwargs)
        attrs["gen_ai.operation.name"] = "text_completion"
        attrs["gen_ai.input.messages"] = _json(
            [{"role": "user", "parts": [{"type": "text", "content": p}]} for p in prompts]
        )
        self._start(run_id, parent_run_id, f"text_completion {model}", SpanKind.CLIENT, attrs, tags)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        attrs: dict[str, Any] = {}
        out_messages: list[dict] = []
        finish_reasons: list[str] = []
        usage: dict[str, Any] = {}
        response_model: Optional[str] = None

        for generation in (response.generations[0] if response.generations else []):
            message = getattr(generation, "message", None)
            gen_info = getattr(generation, "generation_info", None) or {}
            if message is not None:
                msg = _genai_message(message)
                rm = getattr(message, "response_metadata", None) or {}
                finish = rm.get("done_reason") or gen_info.get("finish_reason")
                if finish:
                    msg["finish_reason"] = finish
                    finish_reasons.append(str(finish))
                out_messages.append(msg)
                if not usage:
                    um = getattr(message, "usage_metadata", None) or {}
                    if um:
                        usage = {"input": um.get("input_tokens"), "output": um.get("output_tokens")}
                    elif rm.get("prompt_eval_count") is not None or rm.get("eval_count") is not None:
                        usage = {"input": rm.get("prompt_eval_count"), "output": rm.get("eval_count")}
                response_model = response_model or rm.get("model") or rm.get("model_name")
            else:
                text = getattr(generation, "text", "")
                out_messages.append({"role": "assistant", "parts": [{"type": "text", "content": text}]})

        if not usage and getattr(response, "llm_output", None):
            lo = response.llm_output or {}
            tu = lo.get("token_usage") or lo.get("usage") or {}
            if tu:
                usage = {
                    "input": tu.get("prompt_tokens", tu.get("input_tokens")),
                    "output": tu.get("completion_tokens", tu.get("output_tokens")),
                }

        attrs["gen_ai.output.messages"] = _json(out_messages)
        if finish_reasons:
            attrs["gen_ai.response.finish_reasons"] = finish_reasons
        if response_model:
            attrs["gen_ai.response.model"] = str(response_model)
        if usage.get("input") is not None:
            attrs["gen_ai.usage.input_tokens"] = int(usage["input"])
        if usage.get("output") is not None:
            attrs["gen_ai.usage.output_tokens"] = int(usage["output"])
        self._end(run_id, attrs)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._end(run_id, error=error)

    # ── tools ──────────────────────────────────────────────────────────

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, tags=None, metadata=None, inputs=None, **kwargs):
        tool_name = self._run_name(serialized, kwargs, "tool")
        attrs: dict[str, Any] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool_name,
            "gen_ai.tool.type": "function",
            "gen_ai.tool.call.arguments": _json(inputs) if inputs is not None else str(input_str),
        }
        description = (serialized or {}).get("description")
        if description:
            attrs["gen_ai.tool.description"] = str(description)
        self._start(run_id, parent_run_id, f"execute_tool {tool_name}", SpanKind.INTERNAL, attrs, tags)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        content = getattr(output, "content", output)           # ToolMessage → its content
        self._end(run_id, {"gen_ai.tool.call.result": content if isinstance(content, str) else _json(content)})

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._end(run_id, error=error)

    # ── retrievers (not used by this app; kept so nothing is silently lost) ─

    def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        name = self._run_name(serialized, kwargs, "retriever")
        self._start(run_id, parent_run_id, name, SpanKind.INTERNAL, {"input": _json({"query": query})}, tags)

    def on_retriever_end(self, documents, *, run_id, parent_run_id=None, **kwargs):
        self._end(run_id, {"output": _json({"documents": documents})})

    def on_retriever_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._end(run_id, error=error)


# ── Entry point used by opik_setup.get_run_config ───────────────────────────

def get_otel_callbacks(
    session_id: str,
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> list[BaseCallbackHandler]:
    """
    Callbacks for one study session on the OTel track.

    Also switches the native `@track` decorators off for this process (unless
    OTEL_KEEP_NATIVE_TRACK=true) so the session is traced by OpenTelemetry only.
    """
    configure_otel()
    if not _env_bool("OTEL_KEEP_NATIVE_TRACK"):
        try:
            import opik

            opik.set_tracing_active(False)
        except Exception:
            pass

    handler = OTelLangChainHandler(
        session_id=session_id,
        tags=tags,
        metadata={
            "user_id": "local",
            "model": MODEL,
            "framework": "langgraph",
            "telemetry": "otel",
            "drift_scenario": os.getenv("DRIFT_SCENARIO", "none"),
            "student_mode": os.getenv("STUDENT_MODE", "interactive"),
            **(metadata or {}),
        },
    )
    return [handler]


# ── Cross-process propagation ───────────────────────────────────────────────

def _best_context() -> otel_context.Context:
    """The OTel context to propagate: the current span if live, else the innermost open handler span."""
    current = trace.get_current_span()
    if current.get_span_context().is_valid and current.is_recording():
        return otel_context.get_current()
    with _live_handlers_lock:
        handlers = list(_live_handlers)
    for handler in reversed(handlers):
        span = handler.innermost_open_span()
        if span is not None:
            return trace.set_span_in_context(span)
    return otel_context.get_current()


def inject_trace_headers(headers: Optional[dict[str, str]] = None) -> dict[str, str]:
    """
    Add W3C `traceparent` (and `tracestate`/`baggage` if any) for an outgoing HTTP
    call. Returns the headers dict (a copy). Adds nothing when no trace is active.
    """
    out = dict(headers or {})
    if _provider is None:
        return out
    ctx = _best_context()
    if trace.get_current_span(ctx).get_span_context().is_valid:
        inject(out, context=ctx)
    return out


@contextlib.contextmanager
def server_span(
    name: str,
    headers: Optional[dict[str, str]] = None,
    attributes: Optional[dict[str, Any]] = None,
    agent_name: Optional[str] = None,
) -> Iterator[Span]:
    """
    Continue an incoming trace on the server side (used by the A2A quiz service).

    Extracts W3C `traceparent` from `headers` (case-insensitive); with no header the
    span starts a new trace. The span is made current, so LangChain calls inside the
    block (even through asyncio.to_thread) become its children via the configure hook.
    """
    # Server-side process rules:
    #  * no OpikSpanProcessor: its "@track fallback" would re-home this request's
    #    OTel children into standalone native traces when `@track` helpers run inside;
    #  * native `@track` off (sticky) when this process is on the OTel track, so the
    #    request is traced by OpenTelemetry only. A process started with
    #    TELEMETRY_MODE=native keeps `@track` on; OTel callers then see their W3C
    #    trace intact plus separate native traces for the service's `@track` spans.
    configure_otel(opik_span_processor=_env_bool("OTEL_OPIK_SPAN_PROCESSOR", default=False))
    if os.getenv("TELEMETRY_MODE", "native").lower() == "otel" and not _env_bool("OTEL_KEEP_NATIVE_TRACK"):
        try:
            import opik

            opik.set_tracing_active(False)
        except Exception:
            pass
    hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}
    parent_ctx = extract(hdrs)
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": agent_name or name,
        **(attributes or {}),
    }
    span = get_tracer().start_span(name, context=parent_ctx, kind=SpanKind.SERVER, attributes=attrs)
    handler = OTelLangChainHandler(session_id=None, tags=None, metadata=None, agent_name=agent_name or name)
    with trace.use_span(span, end_on_exit=True, record_exception=True, set_status_on_exception=True):
        with langchain_handler_active(handler):
            yield span


def set_span_json(span: Span, key: str, value: Any) -> None:
    """Set `key` to a JSON string of `value` (used for `input` / `output` on server spans)."""
    span.set_attribute(key, _json(value))


__all__ = [
    "OTelLangChainHandler",
    "configure_otel",
    "export_target",
    "flush_otel",
    "get_otel_callbacks",
    "get_tracer",
    "inject_trace_headers",
    "langchain_handler_active",
    "server_span",
    "set_span_json",
]
