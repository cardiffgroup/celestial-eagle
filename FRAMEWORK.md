# FRAMEWORK.md — durable state for agents whose context forgets

`EVIDENCE.md` is the *rules* (how a claim earns "done"). This is the *machine* those rules run inside: an external memory an agent checks into, so that when its context is compacted or its session restarts, it doesn't quietly continue from a half-erased position.

## The core idea

An agent's context window is short-term memory. On long jobs it gets compacted (summarized to save room) or wiped (restart), and the agent forgets part of what it was doing — a well-known failure on million-token sessions in Claude Code, Muse, and every long-running agent.

So: **treat context as unreliable RAM — a cache that can be silently wrong or half-erased. Treat the external store as the disk: the source of truth.** Two behaviors follow that agents don't do by default:

1. **Write load-bearing state out the moment it's decided** — not when someone asks. A decision, an artifact's real location, a task's done-criteria, the last verified state: to disk immediately, because the context holding it might not survive the next compaction.
2. **Reconcile against the store on wake and on a schedule** — compare what you *think* you're doing against what the store says, and when they diverge, trust the store and report the drift. Anything you "remember" doing that isn't in the journal is ungrounded until re-checked.

## Four layers

**1. State store** (structured, durable, outside the context — and outside any ephemeral sandbox). Not one prose file; separate concerns so an agent can read just what it needs:
- `GOAL` — the one-line objective and its overall done-condition.
- `TASKS` — the task tree: each task has a status, an owner, and an explicit done-criterion (per `EVIDENCE.md`, a criterion an artifact can *test*, not just "done").
- `DECISIONS` — append-only log of choices made and why, so a rehydrated agent doesn't relitigate settled calls.
- `OPEN_QUESTIONS` — what's unresolved and waiting on whom.
- `LAST_VERIFIED` — the most recent grounded state of the world, with the artifacts that prove it and *when* they were checked (evidence has a shelf life).

**2. Evidence journal** — append-only, grounded per `EVIDENCE.md`. Every side-effecting action with the artifact that proves its outcome. This is what survives compaction and restart; it's the difference between "I think I did X" and "the journal shows X done at 14:02 with commit abc123."

**3. Enforcement adapter** (per-platform — this is what makes it a framework, not a habit). The mechanism that maintains layers 1–2 *automatically*, without the model having to remember:
- **Claude Code (grounded against the hooks docs):**
  - `PreCompact` fires *before* compaction (and can even block it) → flush load-bearing state to disk right before the context is compressed. The direct mechanical answer to "it forgot half of what it was doing."
  - `SessionStart` fires on startup/resume/clear/compact with its `source` set accordingly, and its stdout is injected into context → rehydrate from the store on wake. The `source=compact` path is specifically the reinject-after-compaction hook. **Honest caveat — and be precise about which part is documented:** the docs describe that the hook *delivers* text into context. They do **not** state that the model reliably *acts on* that delivered context — that's a separate, and to our knowledge unverified, question. So treat the *delivery* as documented and the *compliance* as untested; don't lean on reinjection as a guarantee — the watchdog's reconcile check is the backstop. (`PostCompact` is a newer after-compaction hook worth using if available.)
  - `PostToolUse` appends side effects to the journal automatically.
  - `Stop` can refuse to let a turn end and hand control back with a reason → the mechanical evidence-gate (don't let "done" stand until the done-criterion's artifact exists). Must gate on a real condition; a Stop hook that always blocks loops forever.
- **Muse / generic Linux agent:** no in-context hooks, so the equivalents run as external plumbing — session-start reads the store from the persistent git repo, scheduled jobs flush/reconcile, the watchdog runs on a host that survives restarts. Shape depends on the agent's real environment (see "Adapters" below).

**4. Watchdog** — a process *external to the agent* that monitors the store for staleness and stalls (a task whose last journal entry is older than its expected cadence, a goal with no progress, a heartbeat gone quiet). One mechanism, two modes via a `mode` config:
- `observe` (default): detect → log → inject a "re-ground against the store" prompt. No state-changing action. Safe; prove it here first.
- `intervene` (opt-in switch): additionally restart a stalled task, resume the last checkpoint, or respawn. Same detection path; the switch just enables the action set. Earn it before you flip it.

## Portable core vs. per-platform adapter

- **Portable (build once):** the state schema, the journal format, the reconcile protocol, the watchdog logic and its mode switch, and the `EVIDENCE.md` rules.
- **Per-platform (one adapter each):** only the *trigger mechanism* — Claude Code hooks vs. Muse's scheduler/gateway vs. anything else. The adapter is the thin part; the core is shared.

## Adapters: grounded, not assumed

An adapter must be built against the target's *real* capabilities, not guessed. For any platform, the adapter design needs answers to:
- Where does the durable store live so it survives a restart? Default assumption for an ephemeral sandbox: **not** inside it — an external repo or host. But "does local disk survive a restart?" is itself a per-platform fact to *test*, not assume: some sandboxes keep a durable home volume across restarts (Muse's does — grounded by an observed reboot), in which case the local store is fine and the repo is an off-box backup rather than the sole truth. Verify it before designing around it either way.
- What fires on session start, so the agent rehydrates before doing anything?
- What can run on a schedule that survives restarts, to host the flush/reconcile/watchdog?
- How does the agent receive the watchdog's "re-ground" prompt (or, in `intervene` mode, its restart)?

Until those are answered from ground truth, the adapter is a hypothesis. Build the core; ground the adapter.

## Reliability: don't let the fix depend on the broken thing

The weakest possible design relies on the agent to keep *itself* on track — read your wake file, reconcile every turn, notice you've drifted. That's circular: the discipline you're trying to enforce is the same discipline that's failing. If recovery depends on the failing component cooperating, it isn't recovery.

Break the circle with two rules:

1. **Separate failure domains.** The watcher must not share fate with the watched. A watchdog *inside* an ephemeral sandbox dies with it. It has to live somewhere else — a persistent host, a different machine, a third-party service.
2. **Absence-detection, not presence-cooperation.** Trigger on the *missing* heartbeat, not on the agent remembering to check in. A dead or drifted agent can't report itself, but an outside watcher noticing silence needs nothing from it. Silence is the signal.

**The escalation ladder** (each rung is a more independent watcher than the last):

- **Heartbeat.** The agent emits a liveness ping every turn (a URL hit, a timestamped file). Cheap, and its *absence* is what everything else keys on.
- **Independent watchdog.** A process in a *separate failure domain* watches for the lapse and escalates: re-inject a recovery turn if it can reach the agent, re-run the last checkpoint, restart a stalled task (this is `intervene` mode, run from outside).
- **Human notification.** If automated recovery doesn't restore the heartbeat within a bound, notify a person over a channel that shares no fate with the stack — a third-party email/SMS/push service whose only job is "tell the human when a ping goes missing."

**The honest floor:** no system fully self-heals. "Who watches the watcher" recurses without end; every design bottoms out somewhere. The correct move is not to pretend otherwise but to (a) make the automated-recovery chain long and independent enough that the human is rarely paged, and (b) make the human-notification hop *bulletproof and independent* — simple, third-party, sharing no fate with anything it monitors. Ending at "a human gets a text" is not a flaw; it is the only honest terminus, and it's where hospitals, aircraft, and datacenters end too. The goal is never "never needs a human" — it's "needs the human only when it truly should, and reliably reaches them then."

A concrete layering, using independent hosts you already have: the agent heartbeats → a persistent box in a different failure domain (e.g. a cloud VM, not the agent's own sandbox) runs the watchdog → a dumb third-party uptime service watches *that box* and emails the human if it goes quiet. Three rungs, three failure domains, terminating in an independent human ping.

### Supervise only what the human can't see

The human-notification rung is **stakes-dependent, and often unnecessary.** Two facts shrink it hard:

1. **A top-level agent's failure is self-announcing.** The agent a person actually converses with doesn't need an alert when it loops or dies — the person is looking right at it, the way you don't page someone to say the screen in front of them is frozen. Automated supervision earns its keep on the parts the human *can't* see: dispatched sub-agents/workers, and autonomous scheduled runs happening while nobody's watching.
2. **Most loops are either self-evident or caught at a turn boundary.** An *interactive* mid-turn hang happens while a person is driving that turn, so they see it. An *autonomous across-turns* loop is caught by a scheduled recovery turn that interleaves at the boundary. Those two cover most of the space.

   **But there's a real gap, and honesty requires naming it: an *autonomous* turn can hang *inside a single turn*, unattended.** Nothing external can preempt a live turn (only a user "stop"), and no human is watching — so that specific failure is neither self-evident nor reachable by the across-turns watchdog until the turn ends or times out. Mitigations exist (a per-turn wall-clock timeout the platform enforces; the heartbeat going stale eventually surfacing it) but they're partial and platform-dependent. Don't paper over it: this is the one case the "self-evident + boundary watchdog" argument does **not** fully cover. It doesn't necessarily justify a notification service — but claiming full coverage without it would be exactly the kind of overclaim this kit exists to stop.

Put together: match the alerting tier to real consequence. A fraud agent moving money needs the page. A personal or home-automation system, interacted with regularly, needs far less — interactive failures are self-evident, across-turns autonomous failures are watchdog-handled, the unattended-mid-turn-hang leans on a platform turn-timeout plus stale-heartbeat detection, and the residual (agent *and* watchdog both down) degrades to "the human notices next time they interact," an adequate backstop at those stakes. Don't build an alerting stack the consequences don't justify — but don't pretend the coverage is total either.
