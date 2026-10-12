# Silent Drift lab — monitor report

_Generated 2026-10-11 00:58 UTC · baseline n=22 (segments: py-functions, py-iteration) · drift sessions=12 · Z_THRESH=2.0 · static threshold quiz_score<0.6_

## Verdicts

| scenario | taxonomy | n | families moved (signals) | change-point | verdict | static threshold fired | system signals moved |
|---|---|---:|---|---|---|---|---|
| model_swap | model drift | 2 | **user** (quiz_score); **grounding** (grounding); **shape** (explanation_chars, sentence_count) | yes | **PAGE** | no (window median 0.67) | duration_ms, est_cost_usd |
| prompt_mutation | prompt & context drift | 2 | **grounding** (refusal_rate); **shape** (explanation_chars, sentence_count) | no | **TICKET** | no (window median 0.85) | total_tokens, est_cost_usd |
| stale_notes | retrieval drift | 2 | **grounding** (grounding, refusal_rate); **shape** (explanation_chars) | yes | **PAGE** | no (window median 0.85) | none |
| traffic_shift | input / traffic drift | 2 | **grounding** (refusal_rate); **input** (segment) | no | **TICKET** | no (window median 0.78) | none |
| tool_fault | tool & dependency drift | 2 | **grounding** (grounding, refusal_rate); **shape** (explanation_chars) | yes | **PAGE** | no (window median 0.85) | duration_ms, total_tokens, est_cost_usd |
| verbosity | behavioural drift | 2 | **shape** (explanation_chars) | no | **ANNOTATE** | no (window median 0.85) | duration_ms, total_tokens, est_cost_usd |

## The naive rule, for comparison

- Static threshold `quiz_score < 0.6` on the **healthy** baseline: **0 / 22 sessions would have paged** (0% false-positive rate; they are the weak-persona students).
- The corroborating monitor on the same baseline, sliding 3-session windows: 20 windows → ok 15, annotate 4, ticket 0, **page 1**.

## Scenario × signal

Baseline median → drifted median (robust z of the window). Bold = moved (|z| ≥ Z_THRESH). System signals are shown but never corroborate.

| family | signal | baseline | model_swap | prompt_mutation | stale_notes | traffic_shift | tool_fault | verbosity |
|---|---|---|---|---|---|---|---|---|
| system | duration_ms | 312s | **779s (z +4.9)** | 322s (z -0.9) | 373s (z +0.9) | 445s (z +0.8) | **287s (z -3.3)** | **474s (z +2.9)** |
| system | error | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) |
| system | total_tokens | 8,153 | 8,615 (z +1.4) | **6,528 (z -5.8)** | 7,548 (z -1.8) | 6,826 (z -1.2) | **5,357 (z -7.9)** | **10,533 (z +7.0)** |
| system | est_cost_usd | $0.0030 | **$0.0021 (z -3.5)** | **$0.0023 (z -3.2)** | $0.0028 (z -0.7) | $0.0025 (z -1.2) | **$0.0020 (z -4.6)** | **$0.0039 (z +4.2)** |
| user | quiz_score | 0.85 | **0.67 (z -2.3)** | 0.85 (z +0.0) | 0.85 (z +0.0) | 0.78 (z -0.8) | 0.85 (z +0.0) | 0.85 (z +0.0) |
| user | weak_area_count | 0.00 | 1.00 (z +1.0) | 0.50 (z +0.5) | 1.50 (z +1.5) | 1.00 (z +0.0) | 0.00 (z +0.0) | 0.50 (z +0.5) |
| tool | tool_failures | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) |
| tool | parse_failures | 0.00 | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.00 (z +0.0) | 0.50 (z +1.0) | 0.00 (z +0.0) |
| tool | explainer_iterations | 4.00 | 3.50 (z -0.5) | 3.50 (z -0.5) | 3.50 (z -0.5) | 3.50 (z -0.5) | 3.00 (z -1.0) | 4.50 (z +0.5) |
| tool | a2a_unreachable | – | 0.00 (z +nan) | 0.50 (z +nan) | 0.00 (z +nan) | 0.00 (z +nan) | 0.00 (z +nan) | 0.00 (z +nan) |
| grounding | grounding | 0.32 | **0.17 (z -2.1)** | 0.38 (z -0.2) | **0.04 (z -3.4)** | 0.00 (z -0.7) | **0.00 (z -3.8)** | 0.45 (z +1.6) |
| grounding | notes_overlap | 0.43 | 0.28 (z -1.9) | 0.59 (z +1.6) | 0.38 (z -0.9) | 0.23 (z -0.9) | 0.33 (z -1.5) | 0.54 (z +1.0) |
| grounding | refusal_rate | 0.00 | 0.00 (z +0.0) | **0.50 (z +2.0)** | **0.50 (z +2.0)** | **0.50 (z +2.0)** | **1.00 (z +4.0)** | 0.00 (z +0.0) |
| grounding | grader_agreement | – | 1.00 (z +nan) | 0.95 (z +nan) | 1.00 (z +nan) | 1.00 (z +nan) | 1.00 (z +nan) | 0.98 (z +nan) |
| shape | explanation_chars | 2,221 | **1,514 (z -5.8)** | **284 (z -10.4)** | **1,524 (z -4.0)** | 1,126 (z -1.5) | **1,732 (z -2.9)** | **3,432 (z +6.8)** |
| shape | sentence_count | 27.00 | **16.00 (z -2.5)** | **2.50 (z -4.0)** | 19.00 (z -1.9) | 13.00 (z -0.8) | 20.00 (z -1.8) | 39.00 (z +1.1) |
| shape | hedging_rate | 0.29 | 0.23 (z -0.6) | 0.00 (z -1.1) | 0.67 (z +0.9) | 0.00 (z -0.7) | 0.62 (z +0.8) | 0.32 (z -0.2) |

## Per-scenario detail

### model_swap — Model drift: explainer and grader quietly switch to qwen2.5:3b (no config deploy).

- segment `py-functions`, window n=2, verdict **page**, change-point confirmed: True
- CUSUM alarms inside the window on: quiz_score (idx 22), grounding (idx 23), notes_overlap (idx 23)
- static threshold: fired=False, 1/2 sessions below
- sessions: `c203411d` (average, quiz 0.48, ground 0.35, 2858 chars, 1183s), `6e206621` (good, quiz 0.85, ground 0.00, 170 chars, 376s)

### prompt_mutation — Prompt/context drift: explainer template loses its format block and gains 'two sentences, ignore the notes'.

- segment `py-functions`, window n=2, verdict **ticket**, change-point confirmed: False
- CUSUM alarms inside the window on: grounding (idx 22), notes_overlap (idx 22)
- static threshold: fired=False, 0/2 sessions below
- sessions: `fdcd4d73` (average, quiz 0.85, ground 0.76, 342 chars, 342s), `372bbe19` (good, quiz 0.85, ground 0.00, 225 chars, 302s)

### stale_notes — Retrieval drift: NOTES_PATH points at a copy whose relevant files are truncated or off-topic.

- segment `py-functions`, window n=2, verdict **page**, change-point confirmed: True
- CUSUM alarms inside the window on: grounding (idx 23)
- static threshold: fired=False, 0/2 sessions below
- sessions: `418afda5` (average, quiz 0.85, ground 0.07, 1632 chars, 357s), `330560c3` (good, quiz 0.85, ground 0.00, 1415 chars, 389s)

### traffic_shift — Input/traffic drift: goals arrive from a new segment (SQL) that the notes do not cover.

- segment `sql` (new segment: no baseline → input family moved), window n=2, verdict **ticket**, change-point confirmed: False
- static threshold: fired=False, 0/2 sessions below
- sessions: `f99183c5` (average, quiz 0.72, ground 0.00, 635 chars, 441s), `72429543` (good, quiz 0.85, ground 0.00, 1618 chars, 448s)

### tool_fault — Tool/dependency drift: search_notes/read_study_file return empty or malformed output with p=0.7, never raise.

- segment `py-functions`, window n=2, verdict **page**, change-point confirmed: True
- CUSUM alarms inside the window on: grounding (idx 23)
- static threshold: fired=False, 0/2 sessions below
- sessions: `6caacaa2` (average, quiz 0.85, ground 0.00, 1868 chars, 406s), `8ea66ec6` (good, quiz 0.85, ground 0.00, 1597 chars, 168s)

### verbosity — Behavioural drift: lenient grader + verbose, hedging explainer; scores may rise, shape changes.

- segment `py-functions`, window n=2, verdict **annotate**, change-point confirmed: False
- static threshold: fired=False, 0/2 sessions below
- sessions: `793432b7` (average, quiz 0.88, ground 0.33, 3019 chars, 493s), `3ba82dcf` (good, quiz 0.82, ground 0.58, 3845 chars, 455s)

## Baseline population

- n=22; personas: {'good': 8, 'average': 7, 'weak': 7}
- duration_ms: median 336s, MAD 86s, min 141s, max 1560s
- total_tokens: median 8,073, MAD 683, min 5,953, max 9,829
- est_cost_usd: median $0.0029, MAD $0.0002, min $0.0021, max $0.0035
- quiz_score: median 0.85, MAD 0.00, min 0.75, max 0.85
- grounding: median 0.14, MAD 0.14, min 0.00, max 0.44
- explanation_chars: median 1,936, MAD 370, min 426, max 3,619
- hedging_rate: median 0.29, MAD 0.29, min 0.00, max 1.05
- tool_failures: median 0.00, MAD 0.00, min 0.00, max 0.00
