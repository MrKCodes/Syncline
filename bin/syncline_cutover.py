#!/usr/bin/env python3
"""
Syncline — one-time cutover: backfill identity markers, then let match-by-key take over (Story 2.1, FR18, AD-3).

Boards imported by the OLD tool carry NO `<!-- syncline-id: KEY -->` marker, so
the new match-by-key engine (Story 2.3) would duplicate every existing issue.
This one-time tool backfills the canonical marker onto each already-imported
issue so the engine can safely switch to marker identity. It is the sole writer
of GitHub during cutover (AD-7).

It classifies the board into exactly three populations and acts on each:
  (a) greenfield       — no state file AND no marker-bearing issues  -> no-op.
  (b) brownfield       — a state file / imported issues exist, NO markers
                         -> run the backfill (dry-run first, then real).
  (c) already-migrated — a marker is already present                 -> no-op.
Markers-present is AUTHORITATIVE over the state file (state is a rebuildable
cache per AD-3): any canonical marker on the board means already-migrated.

The backfill uses the OLD title-matcher as a ONE-SHOT bootstrap: it maps each
plan item's `key` to the existing issue whose title matches, then stamps that
issue's body with the canonical marker. This one-shot title bootstrap is the
ONLY title-based IDENTITY matching left in the codebase after Story 2.1 (the
other title use, `resolve_project`, resolves the PROJECT by title — board
resolution, not item identity, explicitly retained). It never guesses: a plan
key matching 0 or >1 issues by title is reported for manual resolution, not
stamped.

What this story does NOT do (that is Story 2.3): stamp-on-create (FR5),
match-by-key resolution (FR6), or consuming the new `items[]` manifest shape in
import/sync. Here we only relocate the dead title-matcher into this one-shot
bootstrap and backfill markers.

Python 3 stdlib only; all GitHub access via `gh api graphql` (NFR1/NFR2).
Develop and verify against the Story 2.0 scratch board, never a real project.

Usage:
  syncline_cutover.py --config <path>              # perform the backfill
  syncline_cutover.py --config <path> --dry-run    # report would-stamp, no writes
"""
import argparse
import importlib.util
import json
import os

# Reuse the import engine's gh/graphql helpers, load_json, resolve_project — the
# cutover is a sibling tool, not a rewrite of the engine. Load by path so this
# works whether or not `bin/` is on sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_imp = _load_sibling("syncline_import")
_markers = _load_sibling("syncline_markers")

graphql = _imp.graphql
load_json = _imp.load_json
resolve_project = _imp.resolve_project

append_marker = _markers.append_marker
extract_key = _markers.extract_key
index_by_marker = _markers.index_by_marker


# ---------------------------------------------------------------------------
# Pure logic (no GitHub) — unit-tested offline over fixture issue lists.
# ---------------------------------------------------------------------------

def plan_items(state, manifest):
    """Return the ordered list of (key, title) the old tool imported.

    The state file is the AUTHORITATIVE list of what the old tool actually put on
    the board (AD-3), but it carries no titles; the manifest carries key->title.
    So: take the key list from the state file (epics as `epic-N`, stories by
    their `key`) when present, else fall back to the manifest's keys. Resolve
    each key's title from the manifest; a key the manifest doesn't know keeps
    None as its title (it simply can't be title-matched, and is reported)."""
    title_by_key = {}
    for it in (manifest or {}).get("items", []):
        k = it.get("key")
        if not k:  # a malformed manifest item with no key can't be matched — skip it
            continue
        title_by_key[k] = it.get("title")

    keys = []
    if state and (state.get("epics") or state.get("stories")):
        for ekey in state.get("epics", {}):
            keys.append(f"epic-{ekey}")
        for skey in state.get("stories", {}):
            keys.append(skey)
    else:
        keys = list(title_by_key.keys())

    seen = set()
    out = []
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        out.append((k, title_by_key.get(k)))
    return out


def classify(state, issues, items):
    """Classify the board into one of the three cutover populations.

    `state` is the loaded import-state dict (or None if the state file is
    absent). `issues` is the scanned open+closed issue list (each a dict with at
    least "body"). `items` is the plan_items() list (key, title) the cutover
    would back-fill. Returns "greenfield" | "brownfield" | "already-migrated".

    "Already-migrated" means FULLY migrated: EVERY plan item the cutover would
    stamp already carries its marker AND there is nothing left to report. A board
    with SOME markers but items still to stamp (a partially-stamped / interrupted
    run) is brownfield, so the backfill resumes rather than no-op'ing. A single
    marker on an otherwise-unstamped board is therefore brownfield, not already-
    migrated. If any plan item is UNCONFIDENT (0/>1 title match, blank title, or a
    conflicting different-key marker) the board is brownfield too, so the normal
    brownfield path reports those items for manual resolution — an already-migrated
    no-op must never silently swallow unreported items. (The marker stays
    authoritative for an individual issue's identity — the per-issue resumable
    skip in bootstrap_match; only the BOARD-level verdict here uses completeness.
    AD-3: state is a rebuildable cache.)

    Greenfield stays: no state file AND no marker-bearing issues."""
    any_marker = any(extract_key(i.get("body")) is not None for i in issues)
    has_state = bool(state and (state.get("epics") or state.get("stories")))
    if not any_marker and not has_state:
        return "greenfield"
    # At least one marker or state exists. Fully migrated iff nothing is left to
    # stamp (matched empty) AND nothing is left to report (unconfident empty).
    matched, unconfident = bootstrap_match(items, issues)
    if any_marker and not matched and not unconfident:
        return "already-migrated"
    return "brownfield"


def bootstrap_match(items, issues):
    """One-shot title bootstrap: map each plan item key -> the issue to stamp.

    This is the OLD `existing[title.strip()]` title-matcher, relocated here and
    living ONLY here. `items` is a list of (key, title); `issues` is the scanned
    issue list. Returns (matched, unconfident) where:
      - matched = {key: issue} for keys that matched EXACTLY ONE issue by title
        AND whose issue is not already carrying a marker (idempotent/resumable:
        an already-stamped issue is skipped so an interrupted run resumes).
      - unconfident = [(key, reason)] for every key that could NOT be confidently
        matched (title missing/blank, 0 matches, >1 matches, or the single match
        already carries a DIFFERENT key's marker) — reported for manual
        resolution, never guessed/stamped.

    An issue already carrying ITS OWN marker is treated as done (not unconfident):
    it does not appear in either list for that key, so a resumed run skips it."""
    # Index issues by stripped title; a title shared by >1 issue is ambiguous. A
    # blank/whitespace-only title is NOT a usable match target (see below), so it
    # never collects candidates here.
    by_title = {}
    for iss in issues:
        t = (iss.get("title") or "").strip()
        if not t:
            continue
        by_title.setdefault(t, []).append(iss)

    matched = {}
    unconfident = []
    for key, title in items:
        # A missing OR blank/whitespace-only title cannot identify an issue: an
        # empty title would otherwise "match" an untitled issue and false-stamp.
        if not title or not title.strip():
            unconfident.append((key, "no (non-blank) title in manifest to match by"))
            continue
        candidates = by_title.get(title.strip(), [])
        if len(candidates) == 1:
            iss = candidates[0]
            existing_key = extract_key(iss.get("body"))
            if existing_key == key:
                continue  # already stamped with its own marker -> resumable skip
            if existing_key is not None:
                # The title-matched issue already carries a DIFFERENT key's
                # marker: never append a second marker (AD-4 "one marker"). Report
                # for manual resolution instead of stamping.
                unconfident.append(
                    (key, f"issue #{iss.get('number')} already identified as {existing_key!r}")
                )
                continue
            matched[key] = iss
        elif len(candidates) == 0:
            unconfident.append((key, "no issue with a matching title"))
        else:
            nums = ", ".join(f"#{c.get('number')}" for c in candidates)
            unconfident.append((key, f"{len(candidates)} issues share this title ({nums})"))
    return matched, unconfident


# ---------------------------------------------------------------------------
# GitHub-touching driver.
# ---------------------------------------------------------------------------

def scan_issues(owner, repo_name):
    """Scan every open+closed repo issue (id, number, title, body, state).

    Mirrors the issue-pagination scan the old import engine used, adding `body`
    so markers can be read. The cutover reads bodies because the marker lives in
    the body."""
    issues = []
    after = None
    while True:
        page = graphql(
            "query($o:String!,$n:String!,$after:String){ repository(owner:$o,name:$n){"
            " issues(first:100, after:$after, states:[OPEN,CLOSED]){ pageInfo{ hasNextPage endCursor }"
            " nodes{ id number title body state } } } }",
            o=owner, n=repo_name, **({"after": after} if after else {}),
        )["repository"]["issues"]
        issues.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    return issues


def stamp_issue(issue_id, new_body):
    """Append the canonical marker to an issue by rewriting its body (AD-7).

    The cutover is the sole GitHub writer during cutover; it stamps by appending
    to the body via a `updateIssue(body:)` mutation, preserving the existing body
    above the marker (append_marker did the body construction)."""
    graphql(
        "mutation($i:ID!,$b:String!){ updateIssue(input:{id:$i, body:$b}){ issue{ number } } }",
        i=issue_id, b=new_body,
    )


def main():
    ap = argparse.ArgumentParser(description="Syncline one-time cutover (backfill identity markers)")
    ap.add_argument("--config", required=True, help="path to a Syncline config JSON")
    ap.add_argument("--dry-run", action="store_true",
                    help="report which issue each plan item WOULD be stamped onto; write nothing")
    args = ap.parse_args()

    cfg = load_json(args.config, None)
    if cfg is None:
        raise SystemExit(f"Could not read config: {args.config}")

    cfg_dir = os.path.dirname(os.path.abspath(args.config))

    def rel(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg_dir, p))

    owner = cfg["repo_owner"]
    owner_type = cfg.get("owner_type", "org")
    repo_name = cfg["repo_name"]
    project_name = cfg["project_name"]
    state_path = rel(cfg["state_file"])
    manifest_path = rel(cfg["manifest_file"]) if cfg.get("manifest_file") else None

    # The state file is the authoritative record of what the old tool imported.
    # A MISSING state file is a legitimate greenfield factor (None). But a file
    # that EXISTS yet fails to parse must NOT be silently treated as missing —
    # that would misclassify a brownfield board as greenfield and no-op on it. So
    # we parse it directly here and fail legibly on corruption (load_json swallows
    # errors into its default, which is wrong for an existing-but-corrupt file).
    state = None
    if os.path.exists(state_path):
        try:
            with open(state_path) as fh:
                state = json.load(fh)
        except Exception as e:
            raise SystemExit(
                f"State file exists but could not be parsed: {state_path}\n  {e}\n"
                "Refusing to classify (a corrupt state file must not be treated as greenfield)."
            )
    # The manifest supplies key->title for the title bootstrap. A genuinely ABSENT
    # manifest_file is optional (None). But — symmetric with the state file — a
    # manifest path that EXISTS yet fails to parse must fail legibly, not silently
    # become None (which would make every plan item unconfident for a hidden
    # reason rather than naming the corrupt file).
    manifest = None
    if manifest_path and os.path.exists(manifest_path):
        try:
            with open(manifest_path) as fh:
                manifest = json.load(fh)
        except Exception as e:
            raise SystemExit(
                f"Manifest file exists but could not be parsed: {manifest_path}\n  {e}\n"
                "Refusing to classify (a corrupt manifest must not be treated as absent)."
            )

    # Resolve the project (project-by-title board resolution; explicitly retained
    # identity-of-the-board, not of items) and scan the repo's issues.
    proj = resolve_project(owner, owner_type, project_name)
    print(f"Project: {proj['title']} (#{proj['number']}) under {owner_type} {owner}")
    issues = scan_issues(owner, repo_name)
    print(f"Scanned {len(issues)} repo issue(s) (open+closed).")

    # The plan-item list the cutover would back-fill drives both classification
    # (fully-migrated completeness) and the backfill itself.
    items = plan_items(state, manifest)
    population = classify(state, issues, items)

    if population == "greenfield":
        print("Classified: greenfield (no state file, no marker-bearing issues) — nothing to back-fill. No-op.")
        return
    if population == "already-migrated":
        marked = index_by_marker(issues)
        print(f"Classified: already-migrated (all {len(items)} plan item(s) already carry their syncline-id "
              f"marker; {len(marked)} marker-bearing issue(s) on the board) — no-op.")
        return

    # Brownfield: run the backfill via the one-shot title bootstrap. A partially-
    # stamped board lands here too and resumes (bootstrap_match skips the issues
    # already carrying their own marker).
    print(f"Classified: brownfield ({len(items)} plan item(s) to back-fill).")
    matched, unconfident = bootstrap_match(items, issues)

    if args.dry_run:
        print("\n[dry-run] would stamp (plan key -> existing issue it would be stamped onto):")
        if not matched:
            print("  (nothing to stamp — see unconfident/already-stamped below)")
        for key in (k for k, _ in items if k in matched):
            iss = matched[key]
            print(f"  {key}  ->  #{iss.get('number')}  {iss.get('title')!r}")
        if unconfident:
            print("\n[dry-run] could NOT confidently match (manual resolution needed, NOT stamped):")
            for key, reason in unconfident:
                print(f"  {key}: {reason}")
        print("\n[dry-run] no writes performed.")
        return

    # Real run: stamp each confidently-matched issue idempotently; report failures
    # per item and continue (a per-item failure is not fatal to the whole run).
    stamped = 0
    failed = []
    for key in (k for k, _ in items if k in matched):
        iss = matched[key]
        new_body = append_marker(iss.get("body") or "", key)
        if new_body == (iss.get("body") or ""):
            continue  # already carried this marker (resumable) -> skip
        try:
            stamp_issue(iss["id"], new_body)
            stamped += 1
            print(f"  stamped {key}  ->  #{iss.get('number')}")
        except Exception as e:
            failed.append((key, str(e)))
            print(f"  FAILED  {key}  ->  #{iss.get('number')}: {e}")

    print(f"\nBackfill complete: {stamped} issue(s) stamped.")
    if unconfident:
        print(f"{len(unconfident)} plan item(s) could NOT be confidently matched (manual resolution needed):")
        for key, reason in unconfident:
            print(f"  {key}: {reason}")
    if failed:
        print(f"{len(failed)} issue(s) failed to stamp (reported above); re-run to resume.")


if __name__ == "__main__":
    main()
