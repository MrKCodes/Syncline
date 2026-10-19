#!/usr/bin/env python3
"""
Syncline — manifest builder (BMAD-flavoured).

Builds the import/sync manifest (epics + stories with human titles + current
status + epic rollups) from a BMAD-style plan: an `epics.md` (for titles) and a
`sprint-status.yaml` (for per-item status). The importer and the sync both read
this manifest; the sync uses it only to title brand-new items it creates.

This is a CONVENIENCE for BMAD layouts. A project with a different plan format can
hand-write the manifest JSON instead — the importer/sync only require the shape:
  { "epics":  [{ "epic": N, "title": "...", "status": "...", "story_count": K }],
    "stories":[{ "epic": N, "story": M, "key": "N-M-slug", "title": "...", "status": "..." }] }

Config-driven (reuses the Syncline config: epics_file, plan_file, manifest_file,
done_statuses, and the title/line regexes).

Usage:
  syncline_build_manifest.py --config <path>            # write the manifest
  syncline_build_manifest.py --config <path> --print    # print, don't write
"""
import argparse
import glob
import json
import os
import re


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser(description="Syncline manifest builder (BMAD)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--print", action="store_true", dest="print_only")
    args = ap.parse_args()

    cfg = load(args.config, None)
    if cfg is None:
        raise SystemExit(f"Could not read config: {args.config}")
    cfg_dir = os.path.dirname(os.path.abspath(args.config))

    def rel(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg_dir, p))

    epics_md = rel(cfg["epics_file"])
    plan_path = rel(cfg["plan_file"])
    manifest_path = rel(cfg["manifest_file"])
    # Optional: a glob of story spec files whose frontmatter `title:` gives nicer
    # story titles than the sprint key (BMAD writes spec-<key>.md with a title).
    spec_glob = cfg.get("story_spec_glob")
    spec_glob = rel(spec_glob) if spec_glob else None

    done_statuses = set(cfg.get("done_statuses", ["done"]))
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
                txt = open(f).read()
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
    stories = []
    with open(plan_path) as fh:
        for line in fh:
            me = epic_re.match(line)
            if me:
                epic_status[int(me.group(2))] = me.group(3)
                continue
            ms = story_re.match(line)
            if ms:
                key, en, sn, st = ms.group(1), int(ms.group(2)), int(ms.group(3)), ms.group(4)
                prefix = (en, sn)
                title = spec_title_by_prefix.get(prefix) or key
                if title == key:  # fall back to a readable title from the slug
                    title = f"{en}.{sn} " + key.split("-", 2)[-1].replace("-", " ")
                else:
                    title = f"{en}.{sn} {title}"
                stories.append({"epic": en, "story": sn, "key": key, "title": title, "status": st})

    stories.sort(key=lambda r: (r["epic"], r["story"]))

    # Epic rollup: done only if every story is a terminal status (else in_review
    # if any stories, else its own line status).
    epics = []
    for n in sorted(epic_titles):
        kids = [s for s in stories if s["epic"] == n]
        if kids:
            rollup = "done" if all(s["status"] in done_statuses for s in kids) else epic_status.get(n, "in-progress")
        else:
            rollup = epic_status.get(n, "backlog")
        epics.append({
            "epic": n,
            "title": epic_titles[n],
            "status": rollup,
            "story_count": len(kids),
        })

    manifest = {
        "project_name": cfg["project_name"],
        "repo": f"{cfg['repo_owner']}/{cfg['repo_name']}",
        "status_map": cfg["status_map"],
        "epics": epics,
        "stories": stories,
    }

    if args.print_only:
        print(json.dumps(manifest, indent=2))
        return
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"Wrote {manifest_path}: {len(epics)} epics, {len(stories)} stories.")
    for e in epics:
        print(f"  Epic {e['epic']} [{e['status']}] ({e['story_count']}) {e['title']}")


if __name__ == "__main__":
    main()
