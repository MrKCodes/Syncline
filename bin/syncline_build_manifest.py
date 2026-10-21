#!/usr/bin/env python3
"""
Syncline — manifest builder (BMAD-flavoured).

Builds the v1 manifest the engine consumes from a BMAD-style plan: an `epics.md`
(for titles) and a `sprint-status.yaml` (for per-item status). Epics and stories
are emitted into a single flat `items[]` list, each item carrying a durable key —
parents included. This is the one public contract (AD-5): the engine reads only
this shape and never the BMAD plan.

Manifest shape (AD-5 / AD-6):
  { "schema_version": 1,
    "project": "...",
    "repo": "owner/name",
    "status_map": { ... },
    "items": [ { "key": "...", "parent": null|"...", "type": "epic"|"story",
                 "title": "...", "status": "..." }, ... ] }

Hierarchy is carried ONLY by `parent` (a story's `parent` is its epic's key; an
epic's `parent` is null). `type` is an opaque label and encodes no structure.

This is a CONVENIENCE adapter for BMAD layouts. A project with a different plan
format can hand-write a conforming manifest JSON instead — the engine only
requires the shape above.

Config-driven (reuses the Syncline config: epics_file, plan_file, manifest_file,
project_name, repo_owner/repo_name, status_map, and the title/line regexes).

Usage:
  syncline_build_manifest.py --config <path>            # write the manifest
  syncline_build_manifest.py --config <path> --print    # print, don't write
"""
import argparse
import glob
import json
import os
import re


# AD-5 key grammar: durable, marker-safe identity. Must also never contain the
# substring "-->" so a key is safe inside the `<!-- syncline-id: KEY -->` marker
# a later epic stamps.
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")


class ManifestError(Exception):
    """Raised when a plan cannot be turned into a valid manifest. Carries a
    human-legible message naming the offending item(s)/value(s)."""


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def build_manifest(cfg, cfg_dir):
    """Turn a BMAD plan (resolved via `cfg` relative to `cfg_dir`) into a
    conforming v1 manifest dict. Performs the adapter's own pre-write validation
    (status ∈ status_map, key grammar + marker-safety, key uniqueness) and raises
    ManifestError naming the offenders on failure. Never touches GitHub; never
    writes a file."""

    def rel(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg_dir, p))

    epics_md = rel(cfg["epics_file"])
    plan_path = rel(cfg["plan_file"])
    # Optional: a glob of story spec files whose frontmatter `title:` gives nicer
    # story titles than the sprint key (BMAD writes spec-<key>.md with a title).
    spec_glob = cfg.get("story_spec_glob")
    spec_glob = rel(spec_glob) if spec_glob else None

    # The manifest builder needs the NUMBERS out of each line, so it uses its own
    # number-capturing patterns — NOT the config's sync-shaped epic_line_regex /
    # story_line_regex (those capture only key+status, 2 groups). Keep these
    # separate so a project can override the sync patterns without breaking the
    # builder. epic_re groups: (1)=key (2)=epicNum (3)=status.
    # story_re groups: (1)=key (2)=epicNum (3)=storyNum (4)=status.
    epic_re = re.compile(cfg.get("manifest_epic_regex", r"^\s{2}(epic-(\d+)):\s*(\S+)"))
    story_re = re.compile(cfg.get("manifest_story_regex", r"^\s{2}((\d+)-(\d+)-[a-z0-9-]+):\s*(\S+)"))

    # Epic titles from epics.md ("## Epic N: Title").
    epic_titles = {}
    try:
        with open(epics_md) as fh:
            for line in fh:
                m = re.match(r"^## Epic (\d+):\s*(.+)$", line.strip())
                if m:
                    epic_titles[int(m.group(1))] = f"Epic {m.group(1)}: {m.group(2).strip()}"
    except Exception:
        pass

    # Story titles from spec frontmatter, if a glob was given. Keyed by the
    # N-M prefix so we can match a story line to its spec.
    spec_title_by_prefix = {}
    if spec_glob:
        for f in glob.glob(spec_glob):
            try:
                with open(f) as sfh:
                    txt = sfh.read()
            except Exception:
                continue
            tm = re.search(r"^title:\s*'([^']+)'", txt, re.M) or re.search(r'^title:\s*"([^"]+)"', txt, re.M)
            base = os.path.basename(f)
            pm = re.search(r"(\d+)-(\d+)-", base)
            if tm and pm:
                title = tm.group(1).strip()
                # Strip a leading "Story N.M — " prefix for a clean board title.
                title = re.sub(r"^Story\s+\d+\.\d+\s*[—:-]\s*", "", title).strip()
                spec_title_by_prefix[(int(pm.group(1)), int(pm.group(2)))] = title

    # Parse the sprint plan for status.
    epic_status = {}
    story_rows = []  # (epic_num, story_num, key, status)
    with open(plan_path) as fh:
        for line in fh:
            me = epic_re.match(line)
            if me:
                epic_status[int(me.group(2))] = me.group(3)
                continue
            ms = story_re.match(line)
            if ms:
                key, en, sn, st = ms.group(1), int(ms.group(2)), int(ms.group(3)), ms.group(4)
                story_rows.append((en, sn, key, st))

    story_rows.sort(key=lambda r: (r[0], r[1]))

    def story_item(en, sn, key, st):
        title = spec_title_by_prefix.get((en, sn))
        if title:
            title = f"{en}.{sn} {title}"
        else:  # fall back to a readable title from the slug
            title = f"{en}.{sn} " + key.split("-", 2)[-1].replace("-", " ")
        return {
            "key": key,
            "parent": f"epic-{en}",
            "type": "story",
            "title": title,
            "status": st,
        }

    # Assemble the flat items[] list. Epics first (sorted), each immediately
    # followed by its stories (sorted). AD-6: hierarchy lives only in `parent`;
    # `type` is an opaque role label. AD-5 Design Note: an epic's status is read
    # directly from its sprint-status line (no computed rollup); absent → backlog.
    items = []
    for n in sorted(epic_titles):
        items.append({
            "key": f"epic-{n}",
            "parent": None,
            "type": "epic",
            "title": epic_titles[n],
            "status": epic_status.get(n, "backlog"),
        })
        for en, sn, key, st in story_rows:
            if en == n:
                items.append(story_item(en, sn, key, st))

    # Never drop a story: emit any whose epic has no `## Epic N:` heading in
    # epics.md as a trailing item (parent still `epic-<N>`). The engine's AD-10
    # validation catches a dangling parent later; the adapter must not lose it.
    for en, sn, key, st in story_rows:
        if en not in epic_titles:
            items.append(story_item(en, sn, key, st))

    # --- Pre-write validation of the adapter's own guarantees (AC3). ---
    # Fail legibly, naming the offending item(s)/value(s); callers must not write
    # a partial manifest on failure.
    status_keys = set(cfg["status_map"].keys())

    bad_status = [
        (it["key"], it["status"]) for it in items if it["status"] not in status_keys
    ]
    if bad_status:
        offenders = ", ".join(f"{k} (status={s!r})" for k, s in bad_status)
        raise ManifestError(
            "status not present in status_map for item(s): " + offenders
            + f"; allowed statuses: {sorted(status_keys)}"
        )

    bad_keys = []
    for it in items:
        k = it["key"]
        if not KEY_RE.match(k) or "-->" in k:
            bad_keys.append(k)
    if bad_keys:
        raise ManifestError(
            "key(s) violate grammar ^[A-Za-z0-9._-]{1,200}$ (or contain '-->'): "
            + ", ".join(repr(k) for k in bad_keys)
        )

    seen = {}
    dupes = []
    for it in items:
        k = it["key"]
        seen[k] = seen.get(k, 0) + 1
        if seen[k] == 2:
            dupes.append(k)
    if dupes:
        raise ManifestError(
            "duplicate key(s) in manifest: " + ", ".join(repr(k) for k in dupes)
        )

    return {
        "schema_version": 1,
        "project": cfg["project_name"],
        "repo": f"{cfg['repo_owner']}/{cfg['repo_name']}",
        "status_map": cfg["status_map"],
        "items": items,
    }


def main():
    ap = argparse.ArgumentParser(description="Syncline manifest builder (BMAD)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--print", action="store_true", dest="print_only")
    args = ap.parse_args()

    cfg = load(args.config, None)
    if cfg is None:
        raise SystemExit(f"Could not read config: {args.config}")
    cfg_dir = os.path.dirname(os.path.abspath(args.config))

    try:
        manifest = build_manifest(cfg, cfg_dir)
    except ManifestError as e:
        # Fail legibly, exit non-zero, write nothing (AC3).
        raise SystemExit(f"ERROR: cannot build manifest: {e}")

    if args.print_only:
        print(json.dumps(manifest, indent=2))
        return

    def rel(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg_dir, p))

    manifest_path = rel(cfg["manifest_file"])
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    epics = [it for it in manifest["items"] if it["type"] == "epic"]
    stories = [it for it in manifest["items"] if it["type"] == "story"]
    print(f"Wrote {manifest_path}: {len(epics)} epics, {len(stories)} stories.")
    for e in epics:
        kids = sum(1 for s in stories if s["parent"] == e["key"])
        print(f"  {e['key']} [{e['status']}] ({kids}) {e['title']}")


if __name__ == "__main__":
    main()
