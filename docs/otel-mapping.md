# OpenTelemetry → Opik attribute mapping

| | |
|---|---|
| Opik backend | **2.2.96** (self-hosted, `ghcr.io/comet-ml/opik/opik-backend:2.2.96`) |
| Mapping rules | `apps/opik-backend/src/main/java/com/comet/opik/domain/mapping/otel/*MappingRules.java` and `domain/OpenTelemetryMapper.java`, `domain/OpenTelemetryService.java`, `domain/mapping/OpenTelemetryMappingUtils.java` in the Opik repo at tag `2.2.96` |
| Emitter | `app/observability/otel_setup.py` (`OTelLangChainHandler`), opentelemetry-sdk 1.45.1, opik SDK 2.2.96, langchain-core 1.0.0, langgraph 1.1.0 |
| Verified with | `GET /api/v1/private/traces?project_name=silent-drift-lab` and `GET /api/v1/private/spans?trace_id=…` after a real `TELEMETRY_MODE=otel` session (thread `6dccf0d5`, traces `01a127e4-023a-…` and `01a127e6-1af2-…`, 24 spans) plus `scripts/otel_probe.py` runs |

Legend: **✓** = seen in the REST API after a real run; **src** = read from the backend source, not exercised by this app.

## 1. The path

```
LangChain/LangGraph callbacks ──OTelLangChainHandler──▶ OTel spans
   ──BatchSpanProcessor──▶ OTLP/HTTP ──▶ Collector :4318 ──otlphttp──▶ Opik /api/v1/private/otel/v1/traces
                                     └─(OTEL_DIRECT_TO_OPIK=true)────────▶ same endpoint, no Collector
```

Opik's OTLP endpoint is `POST /api/v1/private/otel/v1/traces`, HTTP/protobuf (a JSON variant also exists in `OpenTelemetryResource.java`). Two headers select where spans go: `Comet-Workspace: default`, `projectName: silent-drift-lab` (`RequestContext.PROJECT_NAME`; defaults to "Default Project" when absent). Self-hosted needs no auth.

How the backend turns spans into Opik objects (`OpenTelemetryService.doStoreSpans`):

1. Every OTel trace id in the batch is mapped to an Opik UUIDv7 trace id, cached in Redis under `otelTraceId:<workspace>:<project>:<base64 otel id>` for **3 h** (`OTEL_TTL_INTERVAL`). The first batch for a trace mints it from the earliest span timestamp in that batch.
2. Every span becomes an Opik span. Span ids are `UUIDv7(SHA-256(otel span id), trace timestamp)`, so parent links survive across batches and processes.
3. The **Trace object is created from the root span** (no parent) when that span arrives: name, start/end, input, output, metadata, tags, error and `thread_id` are copied from it. Children that arrived earlier are already stored and attach by trace id. A trace whose root never ends never appears in the UI (it has spans, but no trace row).
4. `metadata.integration` on every span is the **instrumentation scope name** (the tracer name), here `silent-drift-lab.otel-track`.

## 2. Span attributes this app emits → what Opik shows

Rules are matched in order Logfire → GenAI → OpenInference → LiveKit → Pydantic → LiteLLM → **General** → Smolagents → LangFuse (`OpenTelemetryMappingRuleFactory`). The first match wins. Prefix rules strip the prefix for the stored key; exact rules keep the full key.

### LLM spans (`chat <model>`, SpanKind CLIENT)

| Attribute emitted | Type | Opik rule | Opik field | Seen |
|---|---|---|---|---|
| `gen_ai.operation.name` = `chat` | str | GenAI exact → METADATA | `metadata["gen_ai.operation.name"]` | ✓ |
| `gen_ai.system` = `ollama` | str | GenAI exact → PROVIDER, forces `type=llm` | `provider` (authoritative when present) | ✓ `provider: ollama` |
| `gen_ai.provider.name` = `ollama` | str | GenAI exact → PROVIDER (no span type) | `provider` only if `gen_ai.system` absent | ✓ (also echoed as `metadata.provider`) |
| `gen_ai.request.model` | str | GenAI exact → MODEL, `type=llm` | `model` | ✓ `qwen2.5:7b` |
| `gen_ai.request.temperature` | float | GenAI exact → METADATA, `type=llm` | `metadata["gen_ai.request.temperature"]` | ✓ |
| `gen_ai.input.messages` (JSON array string, GenAI message shape) | str | GenAI prefix `gen_ai.input.` → INPUT, `type=llm` | `input.messages` (parsed JSON) | ✓ |
| `gen_ai.output.messages` (JSON array string) | str | GenAI prefix `gen_ai.output.` → OUTPUT, `type=llm` | `output.messages` (parsed JSON) | ✓ |
| `gen_ai.response.model` | str | GenAI exact → MODEL | `model` (overwrites request model if different) | ✓ |
| `gen_ai.response.finish_reasons` | str[] | GenAI exact → METADATA | `metadata["gen_ai.response.finish_reasons"]` | ✓ `["stop"]` |
| `gen_ai.usage.input_tokens` | int | GenAI prefix `gen_ai.usage.` → USAGE, `type=llm` | `usage.prompt_tokens` (renamed by `USAGE_KEYS_MAPPING`) | ✓ |
| `gen_ai.usage.output_tokens` | int | same | `usage.completion_tokens` | ✓ |
| *(not emitted)* `gen_ai.usage.total_tokens` | | | `usage.total_tokens` is **computed by Opik** from the two above | ✓ |
| `server.address` | str | General exact → METADATA | `metadata["server.address"]` | ✓ `localhost` |
| *(not emitted)* `gen_ai.usage.cost` | | GenAI exact → COST | `total_estimated_cost` | src |

Trace-level roll-up (✓): the trace shows `usage = Σ children` (e.g. 7 889 tokens for the resume trace) and `metadata.providers = ["ollama"]`. `total_estimated_cost` stays `null` for `ollama` (no price table). Parent spans that carry usage while their children also do are stripped of usage to avoid double counting (`doStoreSpans`), so usage lives on leaf LLM spans only.

Span name: the handler follows the GenAI semconv `"{operation} {model}"` → `chat qwen2.5:7b` (native `OpikTracer` names the same span `ChatOllama`).

### Tool spans (`execute_tool <name>`, SpanKind INTERNAL)

| Attribute emitted | Opik rule | Opik field | Seen |
|---|---|---|---|
| `gen_ai.operation.name` = `execute_tool` | GenAI exact → METADATA | `metadata["gen_ai.operation.name"]` | ✓ |
| `gen_ai.tool.name` | GenAI prefix `gen_ai.tool.` → METADATA | `metadata.name` | ✓ |
| `gen_ai.tool.type` = `function` | same | `metadata.type` | ✓ |
| `gen_ai.tool.description` | same | `metadata.description` | ✓ |
| `gen_ai.tool.call.arguments` (JSON) | GenAI exact → INPUT, forces `type=tool` | `input["gen_ai.tool.call.arguments"]` (parsed JSON; **full key kept**) | ✓ |
| `gen_ai.tool.call.result` | GenAI exact → OUTPUT, `type=tool` | `output["gen_ai.tool.call.result"]` | ✓ |

### Chain / graph-node spans (SpanKind INTERNAL, type `general`)

| Attribute emitted | Opik rule | Opik field | Seen |
|---|---|---|---|
| `input` = JSON **object** string (LangGraph state) | General prefix `input` → INPUT; empty suffix + object string → fields **merged** into `input` | `input.{messages, goal, roadmap, …}` | ✓ |
| `output` = JSON object string (state update) | General prefix `output` → OUTPUT, merged | `output.{…}` | ✓ |
| `output` for a non-dict value (routing fn returns `"end"`) | wrapped by the handler as `{"value": "end"}` | `output.value` | ✓ (unwrapped scalars land as text with a backend WARN, see §4) |
| `opik.metadata.langgraph_node` / `langgraph_step` / `langgraph_triggers` / `langgraph_path` | General prefix `opik.metadata` → METADATA, prefix stripped | `metadata.langgraph_*` | ✓ |

Node spans in the real run: `curriculum_planner`, `human_approval`, `explainer`, `quiz_generator`, `progress_coach`, plus routing runs `route_after_approval` / `route_after_coach` (LangGraph nests them under the node run). LangGraph's internal runs tagged `langsmith:hidden` (`ChannelWrite<…>`, `__start__`) are skipped by the handler and their children re-parented.

### Root span (one per `graph.invoke()`) → the Opik **Trace**

| Attribute emitted | Opik rule | Trace field | Seen |
|---|---|---|---|
| span name `LangGraph` | — | `trace.name` | ✓ |
| `thread_id` = session id | General exact → THREAD_ID | `trace.thread_id` (grouping in the Threads view) | ✓ |
| `gen_ai.conversation.id` = session id | GenAI exact → THREAD_ID (first THREAD_ID value wins; identical here) | same | ✓ |
| `opik.tags` = `["learning-accelerator","model:qwen2.5:7b","otel-track"]` (array attribute) | General exact → TAGS; accepts a string array, a JSON-array string, or a comma-separated string (`extractTags`) | `trace.tags` and `span.tags` | ✓ |
| `opik.metadata.user_id` / `model` / `framework` / `telemetry` / `drift_scenario` / `student_mode` | General prefix → METADATA | `trace.metadata.*` | ✓ |
| `gen_ai.operation.name` = `invoke_agent` | GenAI → METADATA; the mapper also **forces `type=general`** on invoke_agent spans | `metadata["gen_ai.operation.name"]` | ✓ |
| `gen_ai.agent.name` | GenAI prefix `gen_ai.agent.` → METADATA (stripped) | `metadata.name` | ✓ |
| `gen_ai.framework` = `langgraph` | GenAI exact → METADATA | `metadata["gen_ai.framework"]` | ✓ |
| `input` (initial state, or the resume `Command`) | merged | `trace.input` | ✓ `{"resume": "yes", …}` on the resume trace |
| `output` (final state) | merged | `trace.output` | ✓ |

A session with a human-approval interrupt is two `graph.invoke()` calls → **two traces in one thread** (✓ `01a127e4-023a…` 575 tokens, `01a127e6-1af2…` 7 889 tokens), exactly like the native track.

### Server span in the A2A quiz service (`quiz_service.execute`, SpanKind SERVER)

| Attribute | Opik field | Seen |
|---|---|---|
| parent from W3C `traceparent` | same Opik trace as the caller; `parent_span_id` = caller's span | ✓ probe thread `probe-cdf0fad1` |
| `gen_ai.operation.name` = `invoke_agent`, `gen_ai.agent.name` = `quiz-generator-service` | `metadata` | ✓ |
| `input` / `output` (request/result summaries) | `input` / `output` | ✓ |
| child `chat qwen2.5:7b` from the service's own ChatOllama call | `type=llm`, usage rolled into the caller's trace | ✓ (770 tokens on the probe trace) |

The join works because both processes export to the **same workspace + project**; the Redis key includes the project id, so a service exporting to another project would land in a different trace.

## 3. What Opik drops or does not show

| OTel data | Fate in Opik 2.2.96 |
|---|---|
| Resource attributes (`service.name`, `service.namespace`, `deployment.environment.name`, `telemetry.sdk.*`) | **Dropped.** Only span attributes are mapped; `service.name` appears nowhere (✓). Use `opik.metadata.*` if you need it. |
| Instrumentation scope name | Kept as `metadata.integration` (✓). Scope version is dropped. |
| `SpanKind` (CLIENT/SERVER/INTERNAL) | Dropped (✓). Span type comes from attributes only. |
| Span status `OK` | Dropped. `STATUS_CODE_ERROR` → `error_info` (src: `extractErrorInfo`). |
| `exception` span events (`record_exception`) | → `error_info{exception_type, message, traceback}` (src). Other events → `metadata` via `processEvents` (src). |
| Span links | Dropped (src: never read). |
| `opik.trace_id` / `opik.span_id` / `opik.parent_span_id` | Consumed for linking, then dropped from metadata (General DROP rules). |
| `gen_ai.conversation.id` as its own field | Not shown separately; it becomes `metadata.thread_id` / `trace.thread_id` (✓). |
| `gen_ai.request.temperature` on the trace | Only on the LLM span's metadata, not aggregated (✓). |
| Cost for `ollama` | `total_estimated_cost = null` (✓); only priced providers get a cost. |
| Spans whose trace root never arrives | Stored, but invisible: no trace row (src: `rootSpansByTraceId`). |
| Attributes above `OTEL_ATTR_MAX_CHARS` (200 k, handler-side) | Truncated by the handler → no longer valid JSON → stored as a text value under the attribute key. |

## 4. Rules that bite

* **Unmapped attributes pollute `input`.** Anything with no rule goes to the default bucket, which is `input` (`enrichSpanWithAttributes`). `server.port`, `langgraph.step`, `http.method` would all show up inside the span input. Only emit mapped keys; put custom values under `opik.metadata.<key>`.
* **Logfire's generic names are reserved.** The Logfire rules run first and match bare keys `prompt`, `response`, `result`, `usage`, `params`, `tools`, `tool_arguments`, `tool_response`, `tool_responses`, `final_result`, `all_messages`, `model_name`, `agent_name`. Don't use them for your own attributes.
* **Scalar `input` / `output` strings** (e.g. `"end"`) hit `extractToJsonColumn`'s nested-JSON path and log `Failed to parse nested JSON string for key output … Using as plain text` on the backend (✓ seen once before the handler wrapped scalars as `{"value": …}`).
* **`gen_ai.system` beats `gen_ai.provider.name`.** The deprecated key is authoritative; the new one only fills in when the old one is absent. Emit both while instrumentations are mid-migration (the handler does).
* **`gen_ai.provider.name` carries no span type** (so it can sit on tool/agent spans); an LLM span is typed `llm` by `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*` or `gen_ai.input.*`.
* **`invoke_agent` is forced to `general`** even if the span also carries model/usage attributes.
* **Tags**: array attribute, JSON-array string, or comma-separated string all work (`extractTags`); the handler sends an array.
* **Usage**: int attributes, numeric strings, or a JSON-object string are all accepted; `input_tokens`/`output_tokens` are renamed to `prompt_tokens`/`completion_tokens`; `total_tokens` is computed if missing.
* **Thread id**: first THREAD_ID-mapped attribute wins; the trace only gets it from the **root** span's metadata.
* **Trace creation waits for the root.** With `BatchSpanProcessor`, children export as they end and the root last; the trace row appears when the final flush lands (✓: the 21-minute session's trace was created at 22:40:55, when the root ended). Keep the Redis TTL (3 h) above your longest session.
* **Once a real `TracerProvider` is set, every library's `get_tracer` wakes up.** a2a-sdk (scope `a2a-python-sdk`) traced its event-queue poll loop as a new root span every ~0.5 s, i.e. a new Opik trace each (✓ seen, ~200 junk traces in a few minutes). `otel_setup.ScopeFilteringExporter` drops scopes listed in `OTEL_DROP_SCOPE_PREFIXES` (default `a2a-python-sdk`).

## 5. Native track vs OTel track, as observed

| | native (`OpikTracer`) | OTel track (this handler) |
|---|---|---|
| LLM span name | `ChatOllama` | `chat qwen2.5:7b` |
| `model` / `provider` / `usage` on ChatOllama spans | **`None`** (✓ the SDK has no Ollama usage extractor in `provider_usage_extractors/`) | `qwen2.5:7b` / `ollama` / tokens (✓) |
| Trace usage roll-up | none for Ollama | ✓ |
| Graph rendering (`graph=` in OpikTracer) | ✓ | not available (no equivalent attribute) |
| Feedback scores from code (`log_session_scores`) | ✓ | not wired (`opik_setup` returns early for `MODE != native`) |
| Resume → second trace in the same thread | ✓ (`input: {"__resume__": "yes"}`) | ✓ (`input: {"resume": "yes", …}`) |
| Cross-process hop | `opik_trace_id` / `opik_parent_span_id` headers → `opik.start_as_current_span(opik_distributed_trace_headers=…)` (✓ `probe-0663899e`) | W3C `traceparent` → `server_span()` (✓ `probe-cdf0fad1`) |

## 6. `OpikSpanProcessor` (opik SDK 2.2.96, `opik/integrations/otel/processor.py`)

An OTel `SpanProcessor` whose `on_start` looks for Opik ids to inherit, in order: (1) the parent OTel span's `opik.trace_id` + `opik.span_id` attributes; (2) OTel baggage `opik.trace_id` / `opik.span_id`; (3) the active native `@track` span (`opik_context.get_distributed_trace_headers()`). When found it stamps `opik.trace_id`, a fresh UUIDv7 `opik.span_id` and `opik.parent_span_id` on the new span; the backend then uses those ids **verbatim** (`OpenTelemetryMapper.toOpikSpan` fast path) and does not create a synthetic trace for them (`overriddenTraceIds`). Result: an OTel subtree started inside a `@track` function lands inside that native trace.

Verified: `scripts/otel_probe.py --nest-under-native` → trace `probe.native_parent` (native) containing `probe.session` → `chat qwen2.5:7b` + `execute_tool add` (OTel), usage rolled up (✓ thread `probe-4c565b54`).

Where it is registered in this app: in the main process (default on; `OTEL_OPIK_SPAN_PROCESSOR=false` to disable). **Not** in the quiz service's `server_span()` path: there the fallback (3) would re-home the request's OTel LLM children into standalone `@track` traces when `generate_questions` runs inside the server span (✓ observed before the fix). In the default OTel mode the processor is a no-op anyway, because `get_otel_callbacks()` calls `opik.set_tracing_active(False)` so no `@track` context exists; set `OTEL_KEEP_NATIVE_TRACK=true` to keep `@track` alive and let the processor nest OTel spans under them (expect fragmentation: the LangGraph root is not inside any `@track`, so only the LLM spans inside `@track` helpers move).

## 7. Knobs (`app/observability/otel_setup.py`)

| Env | Default | Effect |
|---|---|---|
| `TELEMETRY_MODE` | `native` | `otel` selects this track (read by `opik_setup`, `a2a_client`, `quiz_service` at call time) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | Collector base URL (`/v1/traces` appended) |
| `OTEL_DIRECT_TO_OPIK` | `false` | `true`: export to `$OPIK_URL_OVERRIDE/v1/private/otel/v1/traces` with the two Opik headers |
| `OTEL_SERVICE_NAME` | `learning-accelerator` | `service.name` resource attribute (dropped by Opik, kept for other backends) |
| `OTEL_OPIK_SPAN_PROCESSOR` | `true` (app) / `false` (server_span) | register `OpikSpanProcessor` |
| `OTEL_KEEP_NATIVE_TRACK` | `false` | keep `@track` active in otel mode |
| `OTEL_DROP_SCOPE_PREFIXES` | `a2a-python-sdk` | instrumentation scopes to drop before export |
| `OTEL_CONSOLE_EXPORT` | `false` | also print spans to stdout |
| `OTEL_ATTR_MAX_CHARS` | `200000` | truncate JSON attributes |
| `OTEL_BSP_SCHEDULE_DELAY` | `2000` | BatchSpanProcessor delay (ms) |

## 8. Commands that reproduce the evidence

```bash
make otel-up                                        # Collector 0.162.0 on :4317/:4318, health on :13133
.venv/bin/python scripts/otel_probe.py              # hand-built trace → Collector → Opik, prints what landed
OTEL_DIRECT_TO_OPIK=true .venv/bin/python scripts/otel_probe.py
.venv/bin/python scripts/otel_probe.py --nest-under-native

TELEMETRY_MODE=otel make services                   # quiz service on the OTel track (:9001)
.venv/bin/python scripts/otel_probe.py --a2a        # W3C traceparent hop into the service
TELEMETRY_MODE=native .venv/bin/python scripts/otel_probe.py --a2a   # opik_* header hop

TELEMETRY_MODE=otel .venv/bin/python main.py "Learn Python closures" --auto-approve --student simulated --max-topics 1 --tag otel-track

# read back
curl -s -G http://localhost:5173/api/v1/private/traces --data-urlencode project_name=silent-drift-lab \
     --data-urlencode 'filters=[{"field":"thread_id","operator":"=","value":"<session id>"}]' -H 'Comet-Workspace: default'
curl -s "http://localhost:5173/api/v1/private/spans?project_name=silent-drift-lab&trace_id=<trace id>&size=200" -H 'Comet-Workspace: default'
```
