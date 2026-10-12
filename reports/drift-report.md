# Silent Drift lab — monitor report

_Generated 2026-10-12 03:13 UTC · baseline n=30 (segments: py-functions, py-iteration) · drift sessions=18 · Z_THRESH=2.0 · static threshold quiz_score<0.6_

## Verdicts

| scenario | taxonomy | n | families moved (signals) | change-point | verdict | static threshold fired | system signals moved |
|---|---|---:|---|---|---|---|---|
| model_swap | model drift | 3 | **shape** (explanation_chars) | no | **ANNOTATE** | no (window median 0.67) | duration_ms, est_cost_usd |
| prompt_mutation | prompt & context drift | 3 | **grounding** (grounding, notes_overlap); **shape** (explanation_chars, sentence_count) | yes | **PAGE** | no (window median 0.77) | duration_ms |
| stale_notes | retrieval drift | 3 | **grounding** (grounding); **shape** (explanation_chars, sentence_count) | no | **TICKET** | no (window median 0.85) | duration_ms |
| traffic_shift | input / traffic drift | 3 | **tool** (empty_searches); **grounding** (refusal_rate); **input** (segment) | no | **TICKET** | no (window median 0.85) | duration_ms |
| tool_fault | tool & dependency drift | 3 | **tool** (empty_searches); **grounding** (grounding, notes_overlap); **shape** (explanation_chars, sentence_count) | yes | **PAGE** | no (window median 0.77) | duration_ms |
| verbosity | behavioural drift | 3 | **grounding** (grounding); **shape** (explanation_chars, sentence_count) | no | **TICKET** | no (window median 0.88) | duration_ms, total_tokens |

## The naive rule, for comparison

- Static threshold `quiz_score < 0.6` on the **healthy** baseline: **1 / 30 sessions would have paged** (3% false-positive rate; lowest baseline quiz_score 0.00).
- The corroborating monitor on the same baseline, sliding 3-session windows: 28 windows → ok 19, annotate 5, ticket 2, **page 2**.

## Scenario × signal

Baseline median → drifted median (robust z of the window). Bold = moved (|z| ≥ Z_THRESH). System signals are shown but never corroborate.

| family | signal | baseline | model_swap | prompt_mutation | stale_notes | traffic_shift | tool_fault | verbosity |
|---|---|---|---|---|---|---|---|---|
| system | duration_ms | 94s | **251s (z +7.6)** | **204s (z +2.9)** | **222s (z +4.6)** | **233s (z +8.0)** | **239s (z +3.8)** | **306s (z +10.2)** |
| system | error | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) |
| system | total_tokens | 8,254 | 7,329 (z -0.5) | 5,717 (z -1.3) | 9,724 (z +0.7) | 6,236 (z -1.7) | 7,182 (z -1.3) | **9,912 (z +2.5)** |
| system | est_cost_usd | $0.0030 | **$0.0019 (z -2.5)** | $0.0021 (z -1.6) | $0.0035 (z +0.8) | $0.0023 (z -1.5) | $0.0026 (z -1.1) | $0.0036 (z +1.8) |
| user | quiz_score | 0.80 | 0.67 (z -1.2) | 0.77 (z +0.0) | 0.85 (z +0.6) | 0.85 (z +0.6) | 0.77 (z -0.4) | 0.88 (z +1.2) |
| user | weak_area_count | 1.00 | 0.00 (z +0.0) | 1.00 (z +0.0) | 0.00 (z -0.7) | 0.00 (z -0.7) | 1.00 (z +0.7) | 1.00 (z +0.0) |
| tool | tool_failures | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) |
| tool | empty_searches | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | **1.00 (z +2.0)** | **1.00 (z +2.0)** | 0.00 (z +0.0) |
| tool | parse_failures | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) |
| tool | explainer_iterations | 4.00 | 3.00 (z -0.7) | 3.00 (z -0.7) | 5.00 (z +0.7) | 3.00 (z -1.0) | 4.00 (z +0.0) | 5.00 (z +0.7) |
| tool | a2a_unreachable | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) |
| grounding | grounding | 0.33 | 0.24 (z -1.0) | **0.75 (z +3.3)** | **0.12 (z -2.3)** | 0.00 (z -1.1) | **0.00 (z -3.3)** | **0.55 (z +2.4)** |
| grounding | notes_overlap | 0.45 | 0.45 (z -0.3) | **0.75 (z +3.6)** | 0.33 (z -1.9) | 0.17 (z -1.8) | **0.27 (z -2.7)** | 0.58 (z +1.7) |
| grounding | refusal_rate | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | **1.00 (z +4.0)** | 0.00 (z +0.0) | 0.00 (z +0.0) |
| grounding | grader_agreement | 1.00 | 0.84 (z -2.0) | 1.00 (z +0.0) | 1.00 (z +0.0) | 1.00 (z +0.0) | 1.00 (z +0.0) | 0.92 (z -1.0) |
| shape | explanation_chars | 2,195 | **1,427 (z -2.8)** | **249 (z -6.4)** | **717 (z -3.2)** | 1,436 (z -0.9) | **688 (z -5.1)** | **3,439 (z +3.2)** |
| shape | sentence_count | 25.00 | 14.00 (z -1.8) | **3.00 (z -3.4)** | **8.00 (z -2.0)** | 15.00 (z -0.4) | **8.00 (z -2.6)** | **46.00 (z +4.6)** |
| shape | hedging_rate | 0.25 | 0.00 (z -0.9) | 0.00 (z -0.9) | 0.42 (z +0.5) | 0.46 (z +1.5) | 0.37 (z +0.3) | 0.00 (z +0.0) |

## Per-scenario detail

### model_swap — Model drift: explainer and grader quietly switch to qwen2.5:3b (no config deploy).

- segment `py-functions`, window n=3, verdict **annotate**, change-point confirmed: False
- CUSUM alarms inside the window on: est_cost_usd (idx 31)
- static threshold: fired=False, 1/3 sessions below
- sessions: `94d5814a` (average, quiz 0.67, ground 0.24, 2079 chars, 227s), `e05fe76c` (good, quiz 0.48, ground 0.42, 1427 chars, 251s), `111540ce` (weak, quiz 0.85, ground 0.00, 128 chars, 408s)

### prompt_mutation — Prompt/context drift: explainer template loses its format block and gains 'two sentences, ignore the notes'.

- segment `py-functions`, window n=3, verdict **page**, change-point confirmed: True
- CUSUM alarms inside the window on: total_tokens (idx 30), est_cost_usd (idx 31), notes_overlap (idx 32)
- static threshold: fired=False, 0/3 sessions below
- sessions: `35a4a305` (average, quiz 0.77, ground 0.76, 348 chars, 204s), `28ffe040` (good, quiz 0.85, ground 0.00, 249 chars, 139s), `e91c5dfe` (weak, quiz 0.77, ground 0.75, 247 chars, 383s)

### stale_notes — Retrieval drift: NOTES_PATH points at a copy whose relevant files are truncated or off-topic.

- segment `py-functions`, window n=3, verdict **ticket**, change-point confirmed: False
- CUSUM alarms inside the window on: total_tokens (idx 31), notes_overlap (idx 31), hedging_rate (idx 30)
- static threshold: fired=False, 0/3 sessions below
- sessions: `1bde90d6` (average, quiz 0.85, ground 0.12, 430 chars, 222s), `6f1fe9d7` (good, quiz 0.85, ground 0.00, 1626 chars, 187s), `77bd895f` (weak, quiz 0.77, ground 0.16, 717 chars, 281s)

### traffic_shift — Input/traffic drift: goals arrive from a new segment (SQL) that the notes do not cover.

- segment `sql` (new segment: no baseline → input family moved), window n=3, verdict **ticket**, change-point confirmed: False
- CUSUM alarms inside the window on: total_tokens (idx 30), est_cost_usd (idx 31), notes_overlap (idx 32)
- static threshold: fired=False, 0/3 sessions below
- sessions: `3b9ddc26` (average, quiz 0.85, ground 0.00, 1513 chars, 233s), `8e244561` (good, quiz 0.85, ground 0.00, 1436 chars, 169s), `d1120427` (weak, quiz 0.85, ground 0.00, 1320 chars, 345s)

### tool_fault — Tool/dependency drift: search_notes/read_study_file return empty or malformed output with p=0.7, never raise.

- segment `py-functions`, window n=3, verdict **page**, change-point confirmed: True
- CUSUM alarms inside the window on: total_tokens (idx 31), est_cost_usd (idx 32), notes_overlap (idx 30)
- static threshold: fired=False, 0/3 sessions below
- sessions: `ced30ca0` (average, quiz 0.80, ground 0.00, 345 chars, 239s), `e889df53` (good, quiz 0.77, ground 0.00, 688 chars, 156s), `0ee40321` (weak, quiz 0.72, ground 0.00, 1671 chars, 338s)

### verbosity — Behavioural drift: lenient grader + verbose, hedging explainer; scores may rise, shape changes.

- segment `py-functions`, window n=3, verdict **ticket**, change-point confirmed: False
- CUSUM alarms inside the window on: total_tokens (idx 32)
- static threshold: fired=False, 0/3 sessions below
- sessions: `5c7bc2b3` (average, quiz 0.88, ground 0.55, 2661 chars, 247s), `222fd0f5` (good, quiz 0.85, ground 0.58, 3952 chars, 306s), `8d501fbe` (weak, quiz 0.90, ground 0.34, 3439 chars, 566s)

## Baseline population

- n=30; personas: {'good': 10, 'average': 10, 'weak': 10}
- duration_ms: median 86s, MAD 13s, min 59s, max 302s
- total_tokens: median 8,041, MAD 736, min 4,682, max 11,636
- est_cost_usd: median $0.0029, MAD $0.0003, min $0.0017, max $0.0043
- quiz_score: median 0.80, MAD 0.05, min 0.00, max 0.85
- grounding: median 0.26, MAD 0.16, min 0.00, max 0.43
- explanation_chars: median 1,952, MAD 386, min 467, max 3,007
- hedging_rate: median 0.00, MAD 0.00, min 0.00, max 0.77
- tool_failures: median 0.00, MAD 0.00, min 0.00, max 0.00
