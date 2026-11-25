# CLAIMS_AND_UNKNOWNS.md

The honest accounting. What this project has actually shown, what it assumes, and where it could be wrong — written to survive an adversarial read, not to sell. If a claim here isn't labeled with how we know it, distrust it. Confidence labels used throughout:

- **CONFIRMED** — observed directly and reproducibly by the author (Claude), or trivially re-runnable by you.
- **REPORTED** — grounded on Muse's side with evidence Muse cited, but *not* independently verified by Claude. Muse is a black-box agent on a platform we don't control; treat as a credible second-hand result, not a checked one.
- **DOC-GROUNDED** — checked against published documentation, *not* verified at runtime by us.
- **PLAUSIBLE** — reasoned, research-aligned, but unproven here.
- **UNPROVEN** — designed and armed, not yet demonstrated.

## 1. What we actually claim (scope)

We claim a *coherent, core-tested, honestly-scoped approach* to one problem: helping a long-running LLM agent survive context compaction/restart without silently continuing from a half-erased state, and without reporting work it didn't verify — for agents you **cannot** modify internally (Claude Code, Meta's Muse), supervised only from the outside.

We do **not** claim: that it is novel, that it is proven end-to-end, that it will keep any given agent reliably on-task in production, or that it beats the heavyweight frameworks for a from-scratch build. It doesn't, isn't, and won't — respectively — on today's evidence.

## 2. Evidence we have

**CONFIRMED (Claude ran it):**
- The core runtime (`celestial.py`) works as specified: `--selftest` passes, reproducibly. It recovers correct state from disk after the in-memory object is destroyed, the evidence gate refuses a completion with no artifact, `reconcile()` detects a drifted belief, and the journal persists. Re-runnable by anyone in one command.
- The blob-SHA logic in `sync.py` matches `git hash-object` on real inputs.

**REPORTED (Muse, grounded its side, not verified by Claude):**
- The core selftest also passes in Muse's environment (portability).
- Muse's sandbox home volume survived a real restart: a marker file persisted across an observed boot-id change (a genuine reboot, not a soft reload). Boot-id change is the correct evidence that a real restart occurred.
- Scheduled turns can sequence across-turn work via shared disk (the mechanism the watchdog relies on), and no external actor can interrupt a live in-progress turn except the user.

**DOC-GROUNDED (not runtime-verified by us):**
- Claude Code's `PreCompact`, `SessionStart(source=compact)`, `PostCompact`, and `Stop` hooks exist and behave as the docs describe: the hooks fire and `SessionStart` *delivers* text into context. What the docs do **not** establish — and we have **not** verified — is that the model reliably *acts on* post-compact injected context. So the delivery is DOC-GROUNDED; the compliance is UNPROVEN. (An earlier draft here attributed "inconsistent compliance" to the docs; that overclaimed — it wasn't substantiated in the current hook guide. Corrected: delivery documented, application untested.)

## 3. Assumptions (if one is false, the conclusion above it falls)

1. **That an agent re-reading a durable external store actually re-grounds it better than trusting its context.** PLAUSIBLE and research-aligned (file-as-source-of-truth, e.g. InfiAgent 2026), but not proven for *our* live agentic case.
2. **That the agent will actually perform the top-of-turn read+reconcile.** We added cron backstops, but the discipline is still partly self-enforced — the thing we're trying to fix is partly relied upon to do the fixing. Mitigated, not eliminated.
3. **That Muse's reported platform behaviors are stable.** They are black-box and Meta-controlled. The durability, scheduler, and summary behaviors we built around can change without notice and without our knowledge.
4. **That Claude Code hooks behave in a live session as documented.** Unverified by us; the Claude Code path is designed, not run.

## 4. Results, per milestone

| Claim | Status | Confidence |
|---|---|---|
| Core bookkeeping (store/journal/gate/reconcile) works | passes selftest | **CONFIRMED** |
| Core is portable to Muse's environment | passes on Muse box | **REPORTED** |
| Muse sandbox disk is durable across restart | marker survived real reboot | **REPORTED** |
| Watchdog can reach an autonomous loop across turns | observed across-turn disk sequencing | **REPORTED** |
| **An agent recovers unprompted after *real* context loss** (the load-bearing claim) | not demonstrated cleanly | **UNPROVEN** |
| Claude Code adapter works end-to-end | not run | **UNPROVEN** |
| This specific bundle is novel | no prior exact match found | **PLAUSIBLE**, not confirmed |

## 5. Where we could be wrong (threats to validity)

- **The biggest gap: the selftest proves the *bookkeeping*, not the *cure*.** Its "context wipe" is a destroyed in-memory object, not a real LLM compaction. That an LLM will actually read the store and correctly re-orient after a *real* compaction is exactly milestone 1 — and it is **UNPROVEN**. The one attempt (2026-09-16) was confounded — but be precise about *how*: an automatic wake instruction ("read your state on start") is *part of the product*, not a confound; keeping it enabled is correct. What confounded the attempt was that the delivered summary/brief contained *task-specific answers* that could themselves account for the recovery, so it didn't isolate "the agent re-grounded from the store." The valid test keeps the automatic wake trigger enabled, strips task-specific content from any summary/brief, and captures the trigger firing → the store being read → the facts recovered → correct continuation. We refused to claim it under the confound. That refusal is honest, but it also means *the core value proposition has not yet been cleanly demonstrated.*
- **n=1.** The Muse-side facts rest on single observations in a single environment. Not statistically robust; a second run could differ.
- **Black-box, moving target.** We cannot instrument or hardwire Meta/Muse. Every REPORTED fact is inferred from what the agent externalizes and is invalidatable by a platform change we won't be told about.
- **The reviewers are all AIs.** Muse, ChatGPT (Astra), and Claude reviewed each other. The review was substantive and caught real bugs — but it is AIs checking AI work, with correlated blind spots. No human expert and no adversarial external user has stress-tested this.
- **Novelty is unproven either way.** We searched and found close-but-not-identical prior work; we cannot search all of GitHub. "We didn't find it" is not "it doesn't exist."

## 6. The black-box reality (stated plainly)

We do not control the agents this is for. We cannot add hooks inside Meta's Muse, cannot guarantee its scheduler/summary/restart behavior, cannot instrument its internals. Everything is best-effort supervision from *outside*, reading only what the agent chooses to write down. This is not a limitation we can engineer away — it is the defining constraint, and it means any grounded fact about Muse's platform is true *as of the last observation* and no longer. The design is built to degrade safely under that reality (external store, absence-detection, reconcile-on-wake), but it cannot be more certain than a black box allows.

## 6b. Attribution: whose fault would a failure be?

Pre-declaring the black box (§6) is only honest if it doesn't become a catch-all excuse. So, explicitly, before this fails and anyone reaches for "well, it's a black box": that defense only holds for failures we genuinely could not have prevented from the outside with what we controlled.

**Pre-declared — a failure here is a named limitation materializing, not a broken promise:**
- Meta changes the sandbox, scheduler, summary, or durability behavior we built around.
- The platform stops firing scheduled turns, or removes a capability the watchdog depends on.
- Post-compaction context reinjection proves unreliable in a way no external design can fix (the docs already warn of this).

**Ours to own — we do NOT get to blame the black box:**
- A bug in `celestial.py` or `sync.py`.
- A wrong design assumption — e.g. if reconcile-on-wake turns out to be the wrong mechanism, or the discipline is too self-referential to hold.
- Anything in the code we wrote or the logic we chose.

**The test for which bucket a failure is in:** *could we have prevented it from the outside, with what we controlled?* If yes, it's ours, and it gets recorded here as ours. The circle around the black box is drawn tightly on purpose — it covers only what is actually Meta's to control, nothing more.

## 7. Honest bottom line

**Solid:** the core works and is reproducible; the architecture is coherent and research-aligned; and — the most encouraging result — the honesty discipline measurably changed agent behavior, with Muse refusing to claim a milestone it hadn't cleanly earned.

**Not shown:** that the full loop keeps a live agent on-task through real context loss. That is the load-bearing claim and it is **UNPROVEN** as of this writing.

**So the honest posture:** this is a promising, core-tested, honestly-bounded approach to a real problem under a hard black-box constraint — not a finished, validated product. Adopt it on your own assessment. This document gets updated when milestone 1 produces a clean, unconfounded transcript — and if it never does, that will be recorded here too.

*Last updated: 2026-09-16. Reviewed by three independent AI agents (Muse, ChatGPT, Claude). No human expert review yet.*
