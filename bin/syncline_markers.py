#!/usr/bin/env python3
"""
Syncline — identity marker primitive (AD-3 / AD-4).

The ONE durable identity mechanism for a Syncline-managed issue. An issue's
identity is a single canonical HTML comment stamped at the END of its body:

    <!-- syncline-id: KEY -->

as the LAST non-empty line of the body, preceded by a blank line that separates
it from the existing body text. `KEY` is the manifest item's `key` (AD-5 grammar;
never contains the substring "-->", so the marker line is unambiguous).

Only a marker in that canonical trailing position counts as identity. Marker-
shaped text anywhere else — in the title, mid-body, or not on its own line — is
IGNORED (position is authoritative, AD-4). This is deliberate: it lets a human
quote a `syncline-id` comment in prose without accidentally re-identifying the
issue.

This module is the sole identity mechanism going forward. The one-time cutover
(Story 2.1) consumes it to backfill markers onto already-imported issues, and the
import/sync rewrite (Story 2.3) will consume it to stamp-on-create and match-by-
key. It is stdlib-only and imports NO engine code so every consumer can share it.
"""
import re


# A canonical marker line: the whole line is `<!-- syncline-id: KEY -->` with
# optional surrounding horizontal whitespace. KEY is captured in group 1. The
# key cannot contain "-->" (AD-5 marker-safety) so `[^\n]+?` + the literal
# closing delimiter is unambiguous. Anchored per-line via re.MULTILINE at the
# call sites that scan a body; `extract_key` additionally enforces that the match
# is the trailing non-empty line.
MARKER_RE = re.compile(r"^[ \t]*<!--[ \t]*syncline-id:[ \t]*(?P<key>[^\n]+?)[ \t]*-->[ \t]*$")


def make_marker(key):
    """Return the canonical marker line for `key` (no surrounding newlines).

    The caller is responsible for positioning it; `append_marker` does that
    correctly. `key` must follow AD-5 grammar (and so must never contain '-->');
    the manifest builder already enforces this on every emitted key."""
    return f"<!-- syncline-id: {key} -->"


def extract_key(body):
    """Return the KEY carried by `body`'s canonical trailing marker, or None.

    Identity is ONLY a marker on its own line as the LAST non-empty line of the
    body (AD-4: position is authoritative). A marker in the title, mid-body, or
    sharing a line with other text is not identity and yields None here."""
    if not body:
        return None
    # The last non-empty, non-whitespace line is the only candidate.
    lines = body.splitlines()
    last = None
    for line in reversed(lines):
        if line.strip():
            last = line
            break
    if last is None:
        return None
    m = MARKER_RE.match(last)
    return m.group("key") if m else None


def append_marker(body, key):
    """Return `body` with `key`'s canonical marker as its trailing line.

    Idempotent: if `body` already carries THIS key in the canonical trailing
    position, `body` is returned unchanged (no second marker). Preserves the
    existing body text above the marker, separated by exactly one blank line.

    If `body` already carries a DIFFERENT key in the canonical position this
    still appends (the caller — the cutover — guards against re-stamping an
    already-identified issue before calling, so this path is not exercised in
    normal use; appending rather than silently rewriting keeps the primitive
    honest and side-effect-free about someone else's marker)."""
    existing = extract_key(body)
    if existing == key:
        return body  # already stamped with this key — idempotent no-op
    marker = make_marker(key)
    if not body or not body.strip():
        return marker
    # Trim trailing whitespace/newlines from the body, then separate the marker
    # with exactly one blank line so it is unambiguously the trailing line.
    trimmed = body.rstrip("\n").rstrip()
    return f"{trimmed}\n\n{marker}"


def index_by_marker(issues):
    """Build {KEY -> issue} over `issues` by their canonical body markers.

    `issues` is any iterable of dict-like issues each exposing a "body". Issues
    with no canonical marker are skipped. On a duplicate key the FIRST issue
    wins (a duplicate marker is a data problem the cutover never creates, since
    it refuses to re-stamp an already-identified issue)."""
    out = {}
    for issue in issues:
        key = extract_key(issue.get("body") if hasattr(issue, "get") else issue["body"])
        if key is not None and key not in out:
            out[key] = issue
    return out
