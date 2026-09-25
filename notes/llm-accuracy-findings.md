# LLM / AI integration — accuracy research findings

Local working notes, not part of the tracked spec (see `.gitignore`). Snapshot of the
codebase as of 2026-09-25. Scope: the four model call sites under `backend/oneguard/`
(`compiler/llm.py`, `engine/tier2.py`, `engine/tier3.py`, `engine/signals.py`) and the
shared provider layer (`llm/provider.py`, `llm/registry.py`, `llm/anthropic_provider.py`,
`llm/openai_provider.py`). Everything here stays inside CLAUDE.md rule 1 — no proposal
gives a model decision power; every item either makes an existing constrained call more
accurate or makes the fallback-to-deterministic path fire less often.

## Current state (for context)

| Site | File | Role | Existing defense |
|---|---|---|---|
| Compiler | `compiler/llm.py` | mandate text → typed rules | `lint_against_floor` (LLM reading must cover everything the regex parser found) + `lint()` (every number/boundary re-derived from raw text by regex, independent of the model's claim) |
| Tier 2 | `engine/tier2.py` | size / return-window extraction, only when regex fails | every model value must be **grounded**: re-found by regex next to its label, or discarded |
| Tier 3 | `engine/tier3.py` | post-decision message rewrite | shop text redacted before the call; rewrite rejected if it drops/adds a number, exceeds 2 sentences, or echoes shop/injection text; template posted first regardless |
| Soft signal | `engine/signals.py` | "is this shop text talking to the agent?" (Laya) | keyword list is the floor; Laya can only ADD a trigger, never clear one; falls back to keywords on any timeout/error |

Provider choice (`gpt-4.1`) was validated once empirically: 75/75 on the 10 served
judging instructions + 5 review sentences (`docs/decisions.md`, 2026-09-24);
`gpt-4o-mini` 70/75, `gpt-4.1-mini` 69/75. Live default is
`ONEGUARD_LLM_PROVIDER=openai` (`.env.example`).

## Findings, priority-ranked

### URGENT

**U1 — The Anthropic path has never been accuracy-tested, but could become the live default at any time.**
`anthropic_provider.py`'s own docstring: "working but not exercised against the live
API in CI." The `gpt-4.1` vs. `gpt-4o-mini` vs. `gpt-4.1-mini` comparison that picked
today's default was never run against `claude-opus-5`. Nobody actually knows whether
Anthropic or OpenAI reads mandates more accurately — the choice was one-sided. This is
urgent because it's a silent gap: `ONEGUARD_LLM_PROVIDER=anthropic` is one env var away
from being flipped (by anyone, at any time) onto a completely unvalidated code path.
**Action:** re-run the existing 75-sentence floor+review benchmark against
`claude-opus-5` (and ideally `claude-sonnet-5` for a cost/latency data point) before
that provider is ever used live.

**U2 — Soft-signal recall against adversarial merchant text is the weakest number in the system.**
Laya spike measured agent-directed-injection recall at only 0.71–0.83 (`docs/decisions.md`).
Because Laya can only add to keywords, never subtract, the ceiling is bounded by the
keyword list. This is the one call site actively defending against an adversarial party
(the merchant), not just parsing an honest customer's mandate — a 71–83% recall there is
a real exposure, not a polish item. **Action:** widen the keyword/regex list against a
corpus of known injection phrasings (cheap, deterministic, raises the floor immediately);
separately evaluate whether an LLM-based classifier fits inside the 500–1000 ms signal
budget as a second OR-only-add signal.

### NECESSARY

**N1 — Compiler effort is hardcoded to `"low"` on Anthropic, same as the much simpler tier-2/tier-3 calls.**
`effort: "low"` is a module constant in `anthropic_provider.py`, justified as "short
extraction calls under a hard deadline" — true for tier 2 (3 grounded fields) and tier 3
(cosmetic, heavily post-validated), but the compiler parses an open-ended mandate against
a ~30-field vocabulary with many interacting edge cases, which is exactly the kind of
task that benefits from higher effort. Effort isn't currently parameterized per call
site. **Action:** make effort a constructor parameter on `AnthropicProvider` (keep `low`
for tier 2/3, raise it for the compiler only), then measure accuracy delta vs. the
8-second compiler timeout budget.

**N2 — No fallback between providers.** `ONEGUARD_LLM_PROVIDER` selects exactly one
provider for every call. A transient timeout/error on it drops straight to the
deterministic floor, even though the other provider (already implemented and tested) is
sitting right there unused. **Action:** try primary → try secondary → floor. Keeps every
existing schema/lint/grounding check intact; just reduces how often the (less
expressive) floor parser fires for a reason unrelated to instruction complexity.

**N3 — The accuracy eval is a one-off n=15 test written into a decision log, not a repeatable regression harness.**
`test_compiler_judging.py` / `test_compiler_recorded.py` pin *recorded* responses (good
for prompt/code drift), but the actual "is the model still accurate" question was
answered once, by hand, and the result lives in `docs/decisions.md` prose. Any future
prompt or model change has no automated way to re-check it. **Action:** build a larger,
versioned eval corpus (adversarial phrasing, multi-language mandates, contradictory
clauses, near-miss boundary wording) with a train/validation/test split, scored
automatically against `lint_against_floor` + hand-labeled expected rules, run whenever
the prompt or model changes.

### LOWER PRIORITY

**L1 — Compiler system prompt (~380 lines, static) isn't cached.** Doesn't affect
accuracy directly, but buys back latency headroom inside the 8 s budget — headroom that
could fund N1's higher effort or a retry-on-schema-failure instead of trading one for
the other.

**L2 — No retry-on-schema-failure for the compiler.** A single failed/off-schema call
goes straight to the floor parser. Given measured P95 ~3.3 s (gpt-4.1) against an 8 s
budget, there may be room for exactly one repair retry before giving up — would need
latency headroom from L1 to be safe under load.

## Explicitly ruled out

Nothing here proposes giving a model decision authority — that breaks CLAUDE.md rule 1.
The lever throughout is: make the calls the model is already trusted with more accurate,
or reduce how often a transient failure forces the (safe, but less expressive)
deterministic fallback.

## Suggested first step

U1: extend the existing benchmark to `claude-opus-5`. Lowest risk, highest information
value — it's the one finding that could change a live default rather than just tune a
knob, and the methodology already exists.
