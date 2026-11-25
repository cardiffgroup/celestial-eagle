#!/usr/bin/env python3
"""
Push a folder of markdown docs to a GitHub repo via the Contents API.
Skips unchanged files (compares local blob sha to remote), auto-discovers
new root-level docs, works against an empty repo. Stdlib only, no deps.

SCOPE — read before relying on it:
  * Single-writer / local-authoritative by design. It fetches the remote
    sha and then PUTs your local content with it, which means it will
    overwrite a remote edit made by anyone else. Use it only where this
    folder is the one source of truth. It is NOT safe for concurrent
    writers and does no conflict resolution.
  * A publisher, not a mirror or an atomic snapshot. Each file is its own
    commit, so a mid-run failure leaves earlier files committed and later
    ones not. Deleting or renaming a local file does NOT remove its old
    remote copy. If you need a consistent point-in-time checkpoint, this
    is not that tool.

Auth: reads a token from the GITHUB_TOKEN env var. A fine-grained PAT
with Contents: read/write on the target repo is enough (Metadata: read
is always-on and covers the default-branch lookup). A classic PAT needs
the `repo` scope instead. Swap the `auth_header()` function if your
platform injects credentials some other way.

Usage:
  export GITHUB_TOKEN=ghp_xxx          # or your platform's mechanism
  # edit the config block below
  python3 sync.py "commit message"
"""
import base64
import hashlib
import json
import os
import stat
import sys
import urllib.parse
import urllib.request
import urllib.error

# ---- config: edit these for your setup ----
OWNER = "your-github-username"
REPO = "your-repo-name"
BRANCH = ""  # leave empty to use the repo's default branch; set only to override
DOCS_DIR = os.path.expanduser("~/workspace/your-docs-folder")
# --------------------------------------------

API = "https://api.github.com"


def auth_header(req):
    # .strip() guards the common `$(cat token.txt)` case — a trailing
    # newline otherwise produces a baffling 401 instead of working.
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set — export it or edit auth_header() for your platform's credential injection")
    req.add_header("Authorization", f"Bearer {token}")


def request(url, method="GET", data=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "celestial-eagle-sync")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    auth_header(req)
    return urllib.request.urlopen(req, timeout=30)


def resolve_branch():
    """The configured branch, or the repo's actual default if none set.
    Prevents a hardcoded 'main' from silently creating a stray branch on a
    repo that defaults to 'master' (or anything else)."""
    if BRANCH:
        return BRANCH
    resp = request(f"{API}/repos/{OWNER}/{REPO}")
    return json.loads(resp.read())["default_branch"]


def remote_sha(fname, branch):
    """Current blob sha of fname on branch, or None if absent.
    fname/branch are percent-encoded so a '#', space, or non-ASCII byte
    can't turn into a URL fragment or hit the wrong path/branch."""
    path = urllib.parse.quote(fname)
    ref = urllib.parse.quote(branch, safe="")
    url = f"{API}/repos/{OWNER}/{REPO}/contents/{path}?ref={ref}"
    try:
        resp = request(url)
        return json.loads(resp.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _read_regular(path):
    """Open a REGULAR file without following a final symlink and without
    blocking on a FIFO, then confirm via fstat before reading. Closes the
    discover->open TOCTOU gap: a file swapped for a symlink or pipe after
    discovery is rejected here (O_NOFOLLOW raises on a symlink; the fstat
    catches a pipe/dir/device)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb", closefd=True) as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise ValueError(f"refusing to publish non-regular file: {path}")
        return fh.read()


def put_file(fname, message, branch):
    raw = _read_regular(os.path.join(DOCS_DIR, fname))
    local_sha = hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()
    rsha = remote_sha(fname, branch)
    if rsha == local_sha:
        print(f"{fname}: unchanged, skipping")
        return None
    payload = {
        "message": message,
        "content": base64.b64encode(raw).decode(),
        "branch": branch,
    }
    if rsha:
        payload["sha"] = rsha  # required when updating an existing file
    path = urllib.parse.quote(fname)
    url = f"{API}/repos/{OWNER}/{REPO}/contents/{path}"
    # The GET (remote_sha) and this PUT are not atomic, and per the SCOPE
    # note this tool is single-writer by design: a remote edit landing in
    # that window is overwritten, not merged. GitHub returns 409 (or 422)
    # if the sha is stale; that aborts loudly with a traceback. Re-running
    # re-reads the now-current sha and overwrites it — so only re-run when
    # your local copy is genuinely the authoritative one.
    try:
        resp = request(url, method="PUT", data=json.dumps(payload).encode())
        return json.loads(resp.read())["commit"]["sha"]
    except urllib.error.HTTPError as e:
        print(f"FAILED {fname}: HTTP {e.code} {e.read().decode()[:300]}")
        raise


def discover(docs_dir):
    """Root-level *.md REGULAR files only.

    Non-recursive on purpose: a recursive glob trades "silently drops a
    subdirectory file" for "silently pushes any *.md anywhere in the tree."
    Regular-file-only on purpose: a symlink (`open()` would follow it out of
    the folder), a directory named `x.md`, or a FIFO `pipe.md` (which can
    block a plain read forever) all get skipped. `put_file` re-checks at open
    time, so a file swapped after discovery is still caught.
    """
    out = []
    for f in sorted(os.listdir(docs_dir)):
        if not f.endswith(".md"):
            continue
        full = os.path.join(docs_dir, f)
        try:
            lst = os.lstat(full)  # no-follow: a symlink shows as a symlink
        except OSError:
            continue
        if not stat.S_ISREG(lst.st_mode):
            print(f"{f}: skipped (not a regular file — symlink/dir/pipe/device)")
            continue
        out.append(f)
    return out


def main():
    if OWNER == "your-github-username" or REPO == "your-repo-name":
        sys.exit("Edit the config block at the top of sync.py first (OWNER/REPO/DOCS_DIR).")
    if not os.path.isdir(DOCS_DIR):
        sys.exit(f"DOCS_DIR does not exist: {DOCS_DIR}")
    message = sys.argv[1] if len(sys.argv) > 1 else "state snapshot"
    files = discover(DOCS_DIR)
    if not files:
        # A checkpoint tool that says "OK" having verified nothing is a
        # false-confidence signal. Make "no files" its own loud outcome.
        sys.exit(f"NOTHING TO SYNC: no eligible *.md files in {DOCS_DIR}")
    branch = resolve_branch()
    changed = 0
    for fname in files:
        sha = put_file(fname, f"{message} ({fname})", branch)
        if sha:
            changed += 1
            print(f"{fname}: commit {sha[:8]}")
    print(f"PUSH OK ({changed} of {len(files)} file(s) updated on {branch})" if changed
          else f"PUSH OK ({len(files)} file(s) verified unchanged on {branch})")


if __name__ == "__main__":
    main()
