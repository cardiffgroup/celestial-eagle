#!/usr/bin/env python3
"""
celestial.py — the portable core of the Celestial Eagle framework.

An agent's context is unreliable short-term memory: it gets compacted or
wiped, and the agent forgets part of what it was doing. This module is the
durable memory it checks into instead. It is platform-agnostic — a Claude
Code hook adapter or a Muse scheduler adapter calls these same functions;
only the *trigger* differs per platform (see FRAMEWORK.md).

Stdlib only. The store is a directory of plain files so it survives
restarts and is readable by a human or another agent:
  state.json      — goal, tasks (+ done-criteria + evidence), open questions,
                    last-verified state
  journal.ndjson  — append-only log of actions and their evidence

Rules enforced mechanically here, not left to the model's goodwill:
  1. A task is 'verified' only when a supplied verifier confirms the
     artifact. A bare citation ("trust me") lands as 'unverified' and stays
     UNFINISHED — it is never silently counted as done (EVIDENCE.md).
  2. reconcile() compares belief against the store in BOTH directions and
     reports drift, so a half-erased context is caught, not continued.

Durability model (single logical writer; cooperating local callers are
serialized by a lock):
  * State writes are atomic (temp file + fsync + os.replace + dir fsync).
  * Each mutation and its journal entry commit together: the entry rides
    inside the atomic state write as a pending marker, is appended+flushed,
    then the marker is cleared. A crash between steps is repaired on load
    (the pending entry is appended, deduped by id). This recovers LOCAL
    bookkeeping only — it must never be used to replay external actions.

NOTE: direct hand-edits of state.json, or other writers that don't take the
lock, do not participate in this protocol. One logical writer at a time.
"""
import json
import os
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone

try:
    import fcntl  # POSIX advisory locking
except ImportError:  # pragma: no cover
    fcntl = None

_EMPTY = {"goal": None, "goal_done": None, "tasks": [],
          "open_questions": [], "last_verified": None, "_pending_journal": None}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_exists(evidence):
    """A built-in verifier: the evidence names a path that actually exists.
    Proves the artifact is *real*, not that it's *correct*. Compose your own
    for other artifact kinds (a command that exits 0, a URL that returns 200,
    a commit that resolves). Pass one to complete_task() to earn 'verified'."""
    return isinstance(evidence, str) and os.path.exists(os.path.expanduser(evidence.strip()))


class StoreCorrupt(RuntimeError):
    """The durable store is unreadable/invalid. Do NOT treat as a fresh
    start — restore from backup. rehydrate() degrades to an emergency
    briefing instead of raising this."""


class _Lock:
    """Best-effort exclusive lock so cooperating local Store callers
    serialize read/modify/write. POSIX flock; a no-op (with a warning) where
    fcntl is unavailable — single-writer discipline still applies."""
    def __init__(self, path):
        self._path = path
        self._fh = None

    def __enter__(self):
        if fcntl is None:
            print("celestial: WARNING no fcntl; file lock is a no-op — keep to one writer",
                  file=sys.stderr)
            return self
        self._fh = open(self._path, "w")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


class Store:
    OPEN_STATUSES = ("todo", "in_progress", "blocked")

    def __init__(self, path):
        # Expand ~ BEFORE abspath, or "~/state" becomes "<cwd>/~/state".
        self.path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(self.path, exist_ok=True)
        self.state_file = os.path.join(self.path, "state.json")
        self.journal_file = os.path.join(self.path, "journal.ndjson")
        self.lock_file = os.path.join(self.path, ".lock")

    # ---- low-level durable IO --------------------------------------------

    def _fsync_dir(self):
        try:
            dfd = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass  # not all platforms/filesystems allow directory fsync

    def _atomic_write_state(self, state):
        fd, tmp = tempfile.mkstemp(dir=self.path, prefix=".state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_file)
            self._fsync_dir()
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _validate_state(obj):
        if not isinstance(obj, dict):
            raise StoreCorrupt("state root is not an object")
        for key, typ in (("tasks", list), ("open_questions", list)):
            if key in obj and not isinstance(obj[key], typ):
                raise StoreCorrupt(f"state['{key}'] is not a {typ.__name__}")
        if "tasks" in obj:
            for t in obj["tasks"]:
                if not isinstance(t, dict) or "id" not in t or "status" not in t:
                    raise StoreCorrupt("a task entry is malformed")
        return obj

    def _read_state_raw(self):
        """Return parsed+validated state, or None if state.json is absent.
        Raises StoreCorrupt on any decode/structure problem."""
        try:
            with open(self.state_file, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return None
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise StoreCorrupt(f"{self.state_file} is not valid UTF-8/JSON ({e})")
        return self._validate_state(obj)

    def load(self):
        """Load state, repairing a pending-journal crash and refusing to
        mistake a lost state file for a fresh project."""
        obj = self._read_state_raw()
        if obj is None:
            # Missing state.json. If the journal has real history, this is a
            # LOST store, not a new one — refuse to silently start fresh.
            if os.path.exists(self.journal_file) and os.path.getsize(self.journal_file) > 0:
                raise StoreCorrupt(
                    f"state.json missing but {self.journal_file} has history — "
                    f"this is a lost store, not a fresh start. Restore state.json "
                    f"from backup before continuing.")
            return dict(_EMPTY)
        # normalize shape
        for k, v in _EMPTY.items():
            obj.setdefault(k, v)
        # repair a crash between the atomic state write and the journal append
        if obj.get("_pending_journal"):
            self._flush_pending(obj)
            obj["_pending_journal"] = None
            self._atomic_write_state(obj)
        return obj

    # ---- journal (append-only, torn-tail tolerant) -----------------------

    def _terminate_torn_tail(self):
        """If the journal's last write was interrupted (no trailing newline),
        close the fragment off on its own line so the next append can't fuse
        onto it. The fragment stays visible (and is skipped, with a warning,
        by journal())."""
        try:
            with open(self.journal_file, "rb") as f:
                if f.seek(0, os.SEEK_END) == 0:
                    return
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    with open(self.journal_file, "a", encoding="utf-8") as a:
                        a.write("\n")
        except FileNotFoundError:
            pass

    def _append_journal(self, entry):
        self._terminate_torn_tail()
        with open(self.journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _flush_pending(self, state):
        """Append state['_pending_journal'] to the journal unless an entry
        with that id is already there (crash-safe, idempotent)."""
        pend = state.get("_pending_journal")
        if not pend:
            return
        have = {e.get("id") for e in self.journal()}
        if pend.get("id") not in have:
            self._append_journal(pend)

    def record(self, action, artifact=None, grounded=False, note=""):
        """Append a standalone action to the journal. `grounded` should be
        True only when `artifact` actually demonstrates the outcome."""
        entry = {"id": uuid.uuid4().hex, "ts": _now(), "action": action,
                 "artifact": artifact, "grounded": bool(grounded), "note": note}
        with _Lock(self.lock_file):
            self._append_journal(entry)
        return entry

    def journal(self):
        """Every parseable, well-shaped record. A torn or malformed line is
        skipped with a warning to stderr — readable history is preserved,
        never silently swallowed, never crashes on bad bytes."""
        out = []
        try:
            with open(self.journal_file, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return out
        for i, raw in enumerate(data.split(b"\n")):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                print(f"celestial: WARNING skipping damaged journal line {i+1}", file=sys.stderr)
                continue
            if not isinstance(rec, dict):
                print(f"celestial: WARNING skipping non-object journal line {i+1}", file=sys.stderr)
                continue
            out.append(rec)
        return out

    # ---- the transactional mutation primitive ----------------------------

    def _commit(self, state, entry):
        """Atomically apply a state change + its journal entry. The entry
        rides inside the state write as a pending marker, is appended and
        flushed, then the marker is cleared — so a crash at any point is
        repaired by load()."""
        state["_pending_journal"] = entry
        self._atomic_write_state(state)          # (1) state + intent, durable
        self._append_journal(entry)              # (2) journal, durable
        state["_pending_journal"] = None
        self._atomic_write_state(state)          # (3) clear the marker

    def _mutate(self, fn):
        """Run fn(state)->entry under the lock, then commit atomically."""
        with _Lock(self.lock_file):
            state = self.load()
            entry = fn(state)
            self._commit(state, entry)

    @staticmethod
    def _task(st, tid):
        for t in st["tasks"]:
            if t["id"] == tid:
                return t
        raise KeyError(f"no such task: {tid}")

    @staticmethod
    def _je(action, note="", artifact=None, grounded=False):
        return {"id": uuid.uuid4().hex, "ts": _now(), "action": action,
                "artifact": artifact, "grounded": bool(grounded), "note": note}

    # ---- goal / tasks / questions ----------------------------------------

    def set_goal(self, goal, done_criterion):
        def fn(st):
            st["goal"] = goal
            st["goal_done"] = done_criterion
            return self._je("set_goal", note=goal)
        self._mutate(fn)

    def add_task(self, tid, title, done_criterion, owner="agent"):
        def fn(st):
            if any(t["id"] == tid for t in st["tasks"]):
                raise ValueError(f"task id already exists: {tid}")
            st["tasks"].append({"id": tid, "title": title, "owner": owner,
                                "status": "todo", "done_criterion": done_criterion,
                                "evidence": None, "evidence_checked": False,
                                "verified_at": None})
            return self._je("add_task", note=f"{tid}: {title}")
        self._mutate(fn)

    def set_status(self, tid, status):
        """Move a task through todo/in_progress/blocked. Reopening a verified
        task CLEARS its verification metadata — it is no longer done."""
        if status in ("verified", "unverified"):
            raise ValueError("use complete_task(tid, evidence, verifier=...) — status is earned, not set")
        if status not in self.OPEN_STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {self.OPEN_STATUSES}")
        def fn(st):
            t = self._task(st, tid)
            was = t.get("status")
            t["status"] = status
            t["evidence_checked"] = False
            t["verified_at"] = None
            note = f"{tid} -> {status}" + (" (reopened; verification cleared)" if was == "verified" else "")
            return self._je("set_status", note=note)
        self._mutate(fn)

    def complete_task(self, tid, evidence, verifier=None):
        """THE EVIDENCE GATE.
          - No evidence            -> refused.
          - verifier given & passes -> status='verified', evidence_checked=True,
                                       journal grounded=True.
          - verifier given & fails/errors -> the failed check is recorded, any
                                       prior verification is CLEARED (status
                                       drops to 'unverified'), then it raises.
          - no verifier            -> status='unverified' (a cited claim, NOT
                                       done): stays UNFINISHED, journal
                                       grounded=False. Earn 'verified' by
                                       passing a verifier.
        """
        if not evidence or not str(evidence).strip():
            raise ValueError(
                f"REFUSED: task {tid} cannot be completed without an evidence "
                f"artifact. Cite the file/output/commit/response, or leave it open.")

        # Run the verifier OUTSIDE the lock (it may be slow / do IO).
        outcome = "cited"        # cited | verified | failed
        err = None
        if verifier is not None:
            try:
                outcome = "verified" if verifier(evidence) else "failed"
            except Exception as e:  # a throwing verifier is a failed check
                outcome, err = "failed", repr(e)

        def fn(st):
            t = self._task(st, tid)
            if outcome == "verified":
                t["status"] = "verified"
                t["evidence"] = evidence
                t["evidence_checked"] = True
                t["verified_at"] = _now()
                return self._je("verify_task", artifact=evidence, grounded=True, note=f"{tid} verified")
            if outcome == "failed":
                # record the failure and clear any earlier success
                t["status"] = "unverified"
                t["evidence"] = evidence
                t["evidence_checked"] = False
                t["verified_at"] = None
                reason = f"{tid} verification FAILED" + (f": {err}" if err else "")
                return self._je("verify_failed", artifact=evidence, grounded=False, note=reason)
            # cited, no verifier
            t["status"] = "unverified"
            t["evidence"] = evidence
            t["evidence_checked"] = False
            t["verified_at"] = None
            return self._je("cite_task", artifact=evidence, grounded=False,
                            note=f"{tid} cited (NOT machine-verified)")

        self._mutate(fn)
        if outcome == "failed":
            raise ValueError(
                f"REFUSED: verifier did not confirm the evidence for {tid} "
                f"({err or 'returned false'}). Task left unverified; the failed "
                f"check is recorded in the journal.")

    def add_question(self, q, waiting_on="human"):
        def fn(st):
            st["open_questions"].append({"q": q, "waiting_on": waiting_on, "ts": _now()})
            return self._je("open_question", note=q)
        self._mutate(fn)

    def set_last_verified(self, summary, artifacts):
        def fn(st):
            st["last_verified"] = {"summary": summary, "artifacts": artifacts, "checked_at": _now()}
            return self._je("set_last_verified", note=summary)
        self._mutate(fn)

    # ---- the two behaviors that fight context loss -----------------------

    def _emergency_brief(self, err):
        """Never crash the wake path. Hand over whatever the journal still
        holds, even if the journal is itself damaged."""
        try:
            j = self.journal()
        except Exception:
            j = []
        tail = "\n".join(f"  - {x.get('ts')} {x.get('action')} {x.get('note','')}"
                         for x in j[-5:] if isinstance(x, dict))
        return ("# REHYDRATE — ⚠ STATE STORE CORRUPT — DO NOT PROCEED AS FRESH\n\n"
                f"{err}\n\n"
                f"Restore state.json from your off-box backup before continuing. "
                f"The append-only journal may still be intact ({len(j)} entries); "
                f"last few:\n{tail}")

    def rehydrate(self):
        """The 'read this first on wake' briefing, built from disk. On a
        corrupt/lost store it degrades to a loud emergency briefing rather
        than crashing — crashing on wake is the worst failure for a framework
        about surviving restarts. UNVERIFIED tasks are listed as UNFINISHED."""
        try:
            st = self.load()
        except StoreCorrupt as e:
            return self._emergency_brief(e)
        lines = ["# REHYDRATE — state from disk, trust this over memory", ""]
        lines.append(f"GOAL: {st.get('goal') or '(none set)'}")
        if st.get("goal_done"):
            lines.append(f"DONE WHEN: {st['goal_done']}")
        lines.append("")
        todo = [t for t in st["tasks"] if t.get("status") != "verified"]
        done = [t for t in st["tasks"] if t.get("status") == "verified"]
        lines.append(f"UNFINISHED ({len(todo)}):")
        for t in todo:
            lines.append(f"  - [{t.get('status')}] {t.get('id')}: {t.get('title')}  (done when: {t.get('done_criterion')})")
        lines.append(f"VERIFIED ({len(done)}):")
        for t in done:
            lines.append(f"  - {t.get('id')}: {t.get('title')}  (evidence [machine-verified]: {t.get('evidence')})")
        if st.get("open_questions"):
            lines.append("")
            lines.append("OPEN QUESTIONS:")
            for q in st["open_questions"]:
                lines.append(f"  - {q.get('q')}  (waiting on: {q.get('waiting_on')})")
        lv = st.get("last_verified")
        if isinstance(lv, dict):
            age = ""
            try:
                dt = datetime.fromisoformat(lv["checked_at"])
                mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
                age = f"  ({mins:.0f} min ago — re-ground if stale)"
            except Exception:
                pass
            lines.append("")
            lines.append(f"LAST VERIFIED WORLD STATE: {lv.get('summary')}{age}")
        return "\n".join(lines)

    def reconcile(self, believed_verified):
        """Compare the agent's belief against the store IN BOTH DIRECTIONS.
        `believed_verified` must be the COMPLETE set of task ids the agent
        thinks it has finished. Returns a list of divergences; empty == belief
        exactly matches the store's verified set.

        Catches (a) claimed-but-not-verified, and (b) verified-on-disk that
        the agent has forgotten — an omitted completion is drift too, and
        should prompt inspection before repeating the work."""
        st = self.load()
        by_id = {t["id"]: t for t in st["tasks"]}
        believed = set(believed_verified)
        drift = []
        for tid in believed:
            t = by_id.get(tid)
            if t is None:
                drift.append(f"believe {tid} done, but no such task exists in the store")
            elif t.get("status") != "verified":
                drift.append(f"believe {tid} done, but store has it as '{t.get('status')}' with no verified evidence — RE-CHECK before continuing")
        for t in st["tasks"]:
            if t.get("status") == "verified" and t["id"] not in believed:
                drift.append(f"store has {t['id']} verified, but you don't believe it's done — you may have forgotten it; INSPECT before repeating the work")
        return drift


# --------------------------------------------------------------------------
def _selftest():
    """A real test: exits non-zero if any check fails. Covers the evidence
    gate (verified vs cited vs failed), recovery from a wiped/corrupt/lost
    store, torn journals, pending-journal crash repair, bidirectional
    reconcile, and home-path expansion."""
    import tempfile as _tf, shutil, io, contextlib
    d = _tf.mkdtemp(prefix="ce_selftest_")
    failures = []

    def check(cond, label):
        print(("  ok:   " if cond else "  FAIL: ") + label)
        if not cond:
            failures.append(label)

    try:
        s = Store(d)
        s.set_goal("Ship v1", "framework installed and demo passes")
        s.add_task("t1", "core", "output file contains PASS")
        s.add_task("t2", "adapter", "recovers after real compaction")

        # A good verifier tests the task's ACTUAL criterion, not mere
        # existence (existence isn't proof — EVIDENCE.md). Here the criterion
        # is "the output file contains PASS", so the verifier reads it.
        out = os.path.join(d, "t1_output.txt")
        with open(out, "w") as f:
            f.write("PASS\n")
        content_ok = lambda ev: os.path.isfile(ev) and open(ev).read().strip() == "PASS"
        s.complete_task("t1", out, verifier=content_ok)
        check(Store._task(s.load(), "t1")["status"] == "verified", "content-testing verifier -> verified")

        print("=== evidence gate ===")
        try:
            s.complete_task("t2", "")                       # empty -> refused
            check(False, "gate refuses empty evidence")
        except ValueError:
            check(True, "gate refuses empty evidence")

        # a bare citation is NOT done: it stays unfinished as 'unverified'
        s.complete_task("t2", "trust me")
        t2 = Store._task(s.load(), "t2")
        check(t2["status"] == "unverified", "bare citation -> 'unverified' (stays unfinished)")
        check("t2" not in [x["id"] for x in s.load()["tasks"] if x["status"] == "verified"],
              "cited task is NOT counted verified")

        # failed recheck clears a prior success and records the failure
        s.complete_task("t1", d, verifier=file_exists)      # re-verify ok
        try:
            s.complete_task("t1", "/no/such/path", verifier=file_exists)  # now fails
            check(False, "failed recheck raises")
        except ValueError:
            check(True, "failed recheck raises")
        t1 = Store._task(s.load(), "t1")
        check(t1["status"] == "unverified" and t1["evidence_checked"] is False,
              "failed recheck clears the earlier verification")
        check(any(e["action"] == "verify_failed" for e in s.journal()),
              "failed recheck is recorded in the journal")

        # reopening a verified task clears its verification
        s.complete_task("t2", d, verifier=file_exists)
        s.set_status("t2", "in_progress")
        check(Store._task(s.load(), "t2")["verified_at"] is None,
              "reopening a task clears verified_at")

        # ---- restart: rebuild only from disk ----
        del s
        s2 = Store(d)
        reh = s2.rehydrate()
        check("REHYDRATE" in reh and "GOAL: Ship v1" in reh, "rehydrate rebuilds from disk")

        # bidirectional reconcile: forgotten completion is drift too
        s2.complete_task("t1", d, verifier=file_exists)     # t1 verified again
        drift_forgot = s2.reconcile([])                     # believe nothing
        check(any("t1" in x and "forgotten" in x for x in drift_forgot),
              "reconcile catches a FORGOTTEN completion (bidirectional)")
        drift_claim = s2.reconcile(["t2"])                  # t2 is unverified
        check(any("t2" in x for x in drift_claim), "reconcile catches a claimed-but-unverified task")

        # torn journal tail: a partial line does not swallow the next entry
        with open(s2.journal_file, "a") as f:
            f.write('{"partial": true, no newline')
        s2.record("after_torn", note="must survive")
        check(any(e.get("action") == "after_torn" for e in s2.journal()),
              "a torn journal tail does not swallow the next entry")

        # pending-journal crash repair: simulate a crash after state write,
        # before the journal append
        stt = s2.load()
        pend = {"id": uuid.uuid4().hex, "ts": _now(), "action": "crashed_entry", "note": "x"}
        stt["_pending_journal"] = pend
        s2._atomic_write_state(stt)                         # crash right here
        s3 = Store(d)
        recovered = s3.load()                               # load() must repair
        check(recovered["_pending_journal"] is None and
              any(e.get("id") == pend["id"] for e in s3.journal()),
              "crash between state-write and journal-append is repaired on load")

        # structural corruption -> emergency brief, not a crash
        with open(s3.state_file, "w") as f:
            f.write("null")
        with contextlib.redirect_stderr(io.StringIO()):
            em = s3.rehydrate()
        check("CORRUPT" in em.upper(), "structural corruption -> emergency briefing (no crash)")

        # missing state.json but journal present -> lost, not fresh
        os.remove(s3.state_file)
        try:
            s3.load()
            check(False, "missing state with journal history is refused as 'lost'")
        except StoreCorrupt:
            check(True, "missing state with journal history is refused as 'lost'")

        # home-path expansion: Store('~/...') must not create a literal ./~/
        hp = Store("~/.celestial_selftest_probe")
        check("/~/" not in hp.path and hp.path.startswith(os.path.expanduser("~")),
              "home-relative path is expanded, not taken literally")
        shutil.rmtree(hp.path, ignore_errors=True)

        print()
        if failures:
            print(f"SELFTEST FAILED — {len(failures)} check(s) failed")
            sys.exit(1)
        print("SELFTEST OK — all checks passed")
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
    else:
        print("celestial.py — framework core. Run with --selftest to see it work.")
        print("Import Store from this module; wire it to your platform's")
        print("session-start / pre-compact / post-tool / stop events (see FRAMEWORK.md).")
