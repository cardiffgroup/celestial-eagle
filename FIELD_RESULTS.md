# Field results — errors the kit caught in the wild

Depersonalized excerpts from a live eight-hour agentic session (Claude Code cold test). Names, accounts, dates, locations, and device identifiers have been removed; the error patterns and the catches are preserved intact. Each entry: what was claimed, what was actually true, which rule caught it.

## 1. Existence isn't proof
A device node existed at its expected path but returned ENXIO on open. Hours were spent treating the node's existence as the subsystem working before the failure was caught. Rule applied: the existence of an artifact is not evidence of function.

## 2. The non-relay rule
Two agent sessions were positioned to drive the same serial device. A claim mismatch surfaced before either session acted, and the second stood down. Without the rule, both would have driven the port.

## 3. The author caught himself
An agent reported it had released a device-claim lock, citing its own edited working copy as the state of record. The push had actually been denied; the remote still showed the lock held. Re-checking against the remote — rather than answering from memory — surfaced the drift. "Existence isn't proof," applied to the agent's own output.

## 4. State can vanish mid-session
Native task tooling was revoked underneath a live session — no restart, no compaction. Everything living only in native tooling was lost. Lesson: the journal is the state of record because the platform's memory is not.

## 5. Provenance is a claim too
Authority-shaped text arrived through the user channel, referencing a response the agent had never made. Formatting isn't provenance. The rule that caught it: treat claims about who said what with the same skepticism as claims about what is true.

---
*These are field notes, not testimonials. Each entry is checkable against the session record it came from.*
