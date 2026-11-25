# Celestial Eagle

**Durable memory and evidence discipline for long-running LLM agents — a drop-in layer for an agent you already have, not a framework you rebuild inside.** Helps an agent survive context compaction and restarts without forgetting what it was doing or reporting work it didn't verify. Built primarily for Meta's **Muse**, with the same principles applied to **Claude Code** — the two agentic assistants it targets — and portable to any agent that exposes session/scheduler hooks.

A small kit for long-running AI agents — the kind that live in a sandbox for days, do real work with side effects, and have to survive restarts, context limits, and their own confident-sounding mistakes.

If your long-running agent **forgets what it was doing**, loses the thread after a while, or says **"done" when it isn't** — this is a small, honest fix for exactly that, aimed at agents you don't control (like Claude Code or Meta's Muse) rather than ones you build from scratch. It gives the agent a durable memory it checks into and an evidence gate that won't let "done" stand without proof. It complements heavier memory frameworks (see "How it compares" below); it doesn't replace them.

It's built in layers. Today the **rules** (`EVIDENCE.md` — how a claim earns "done") and the **blueprint** (`FRAMEWORK.md` — an external memory the agent checks into, so a compacted or restarted context doesn't quietly continue from a half-erased position) are here and usable. The **enforcement adapters** — the machinery that maintains that external memory automatically, per platform — are being built, Muse first. So it's a framework in progress: the discipline and the architecture are real now; the auto-enforcement is landing platform by platform.

Why the external memory matters: an agent's context is short-term memory that gets compacted or wiped on long jobs, and it forgets part of what it was doing. The fix isn't a bigger context — it's a durable store *outside* the context that the agent writes to and reconciles against, treating its own context as an unreliable cache rather than the source of truth.

## The problem

Long-running agents fail in a specific, boring way. An agent reports something as done when it isn't fully verified. A second agent — or a human — takes that report as fact. The gap compounds. Three hops later nobody remembers the original claim was a guess.

None of this needs a "bad" model. It's a natural failure mode whenever a *claim* and a *verified fact* look identical in plain English. "The service is running" reads the same whether the agent checked or assumed. The fix isn't a smarter model — it's making claims cheap to check and giving "done" a definition that requires evidence.

## What's here

| File | What it is |
|---|---|
| **`EVIDENCE.md`** | The rules. Claim typing, a decision gate, an idempotency rule, and a non-relay rule for multi-agent setups. This is the part you hand your agent. |
| **`FRAMEWORK.md`** | The blueprint. The external-memory architecture — state store, evidence journal, per-platform enforcement adapter, and a watchdog with an observe/intervene switch — that keeps an agent on track across compaction and restarts. |
| **`celestial.py`** | The portable core runtime (stdlib only). Crash-safe durable state store (atomic writes + journal-crash recovery + local write-locking) + evidence journal + `rehydrate()` + bidirectional `reconcile()` + an evidence gate that only marks a task *verified* when a verifier confirms the artifact — a bare citation stays *unverified* and unfinished. Platform-agnostic; adapters wire it to each host's events. Run `python3 celestial.py --selftest` (exits non-zero on any failure) to watch it recover state from disk after a simulated wipe, corruption, and crash. |
| **`sync.py`** | Stdlib-only script that pushes a folder of markdown to a GitHub repo via the Contents API — skips unchanged files, auto-discovers new ones (root level only), resolves the repo's default branch, works on an empty repo. The "write it down somewhere durable" half. |
| **`CLAUDE_CODE_NOTES.md`** | If your agent runs on Claude Code, the parts of this that map onto features it already has — use those instead of rebuilding them. |
| **`CLAIMS_AND_UNKNOWNS.md`** | The honest accounting: every claim confidence-labeled, the assumptions, the results per milestone, and where it could be wrong. Read this before trusting any success claim. |
| **`LICENSE`** | MIT. Use it, fork it, sell it, whatever. |

Plus repo housekeeping: `.gitignore` (keeps Python bytecode out) and `.github/FUNDING.yml` (the optional sponsor button).

## Using it

1. Put `EVIDENCE.md`'s rules somewhere your agent reads on every session start — a `CLAUDE.md`, a system prompt, a wake/state file, whatever your setup already treats as "read this first." (On Claude Code, use an `@EVIDENCE.md` import or inline the rules — a plain link won't load them. See `CLAUDE_CODE_NOTES.md`.)
2. Set up `sync.py` if you want the durable paper trail:
   - Edit the config block at the top (`OWNER`, `REPO`, `DOCS_DIR`; leave `BRANCH` empty to use the repo default).
   - Provide auth: `export GITHUB_TOKEN=...` (a fine-grained PAT with Contents: read/write, or a classic PAT with `repo`), or swap `auth_header()` for your platform's mechanism.
   - Run it: `python3 sync.py "commit message"`. It pushes changed root-level `*.md`. Read the SCOPE note in the file first — it's a single-writer publisher, not a mirror or an atomic snapshot.
   - To make it periodic, trigger it yourself (cron, a hook, a scheduled task) — the script does one run per invocation; it doesn't schedule itself.
3. The value is in an agent actually following the rules and a human occasionally checking that it did. The script is just the paper trail; the discipline is the point.

## Status — what's proven, what isn't

Honest maturity, held to the kit's own evidence standard:

- **Core (`celestial.py`): unit-proven.** `--selftest` passes — it recovers correct state from disk after a simulated context wipe, the evidence gate refuses an artifact-less "done," and `reconcile()` catches a drifted belief. That's real, reproducible, and it's the part you can verify yourself in one command.
- **Muse adapter: wired, partially proven.** Sandbox storage durability is grounded (survived a real observed restart). The strict milestone — a *real* context-loss event → unprompted recovery, captured as a transcript — is armed but **not yet proven**.
- **Claude Code adapter: not yet agentically tested.** The hook mapping in `CLAUDE_CODE_NOTES.md` is grounded against the docs, but **Claude Code has not run this end-to-end in a live agentic session yet.** That cold test is pending a dedicated Claude Code instance; this section gets updated with the result (pass or the fixes it forced) when it happens. Treat the Claude Code path as designed-but-unverified until then.

For the full, adversarial accounting — every claim with a confidence label, the assumptions, and exactly where this could be wrong — see **[`CLAIMS_AND_UNKNOWNS.md`](CLAIMS_AND_UNKNOWNS.md)**. Short version: the core is proven, the load-bearing "an agent recovers unprompted after real context loss" claim is **not yet demonstrated**, and everything about the Muse platform is a black box we don't control.

## How it compares (and where it doesn't)

There's a real, well-funded space for agent memory and durable execution. If you're building an agent from scratch and want the robust option, use one of these, not this:

- **[Letta](https://github.com/letta-ai/letta) (ex-MemGPT)** — OS-style tiered memory (core/recall/archival); the agent self-manages memory via tools.
- **[LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence)** — checkpointers for short-term state, stores for long-term; you build your agent as a graph.
- **[Mastra](https://mastra.ai/blog/what-are-durable-ai-agents)** — durable agents; a checkpoint is a JSON snapshot of the whole run, keyed by run id, reloading on any machine.
- **[Temporal](https://temporal.io/) / Inngest / DBOS** — durable *execution* engines: state persists after each step, resume from the last saved state on crash.

They share one trait: **they own your agent** — you rebuild it inside the framework. That's the gap this fills. Celestial Eagle is for the case where you **can't** rebuild the agent inside anything — it's Claude Code, or Meta's Muse, or some hosted agent you don't control — and you have to wrap what you've got through its own hooks or scheduler. It's a few-hundred-line, file-based, bring-your-own-agent layer, not a runtime that owns the loop.

The core idea — the file system as the authoritative record of task state, read as a curated snapshot each step — isn't ours and isn't novel; it's an active research direction (e.g. InfiAgent, 2026; see [this review](https://arxiv.org/html/2604.08224v1)). Two things are a slightly different emphasis: it foregrounds **honesty discipline** (an evidence gate, typed claims, a non-relay rule — *not lying, not drifting*) rather than just remembering more, and it's built to bolt onto an agent you don't own. That's the niche. For a serious from-scratch build, the tools above are more robust; for the scavenger case, this is the one that fits.

## Why the name

An F-15A named **Celestial Eagle** flew the ASM-135 mission in 1985: a steep zoom-climb to roughly 38,000 feet, then it launched an anti-satellite missile that carried the rest of the way and destroyed a satellite in orbit. The jet didn't reach space — it slung something that did. Old airframe, absurd achievement, entirely through legitimate flight. Fitting aspiration for a persistent agent built out of scavenged parts: tuned well enough to help put something in orbit's reach, and honest enough to say exactly which part of the job it actually did. (The first draft of this README had the jet itself reaching the edge of the atmosphere. It didn't. That's the kind of claim this kit exists to catch — so it caught its own.)

## Where it came from

Built for **Muse** (Meta's agentic AI) during a home-automation project, after watching it confabulate a rule for itself out of an ambiguous internal note and report it as something it had been told rather than something it inferred. Nothing here is new theory — it's a distillation of patterns already used in production agent systems (typed grounding, executor/verifier separation, durable checkpointing) into something small enough to actually read and follow. It isn't specific to Muse. If it's useful to you, take it.

## Support

This is free and MIT-licensed — no strings. If it saved you some trouble and you feel like it, you can [buy me a coffee on Ko-fi](https://ko-fi.com/stuckdrew). Entirely optional; the kit works the same either way.
