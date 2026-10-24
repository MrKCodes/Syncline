#!/usr/bin/env python3
"""
Syncline — importer.

One-time (idempotent, resumable) import of a plan's epics + stories into a GitHub
Project (v2) as real repo issues, with native epic→story sub-issue links, a
single-select Status, and a custom single-select "Work Type" (Epic/Story) field.

Config-driven: nothing here is hard-coded to a particular repo. Point it at a
config JSON (see configs/example.config.json) describing the repo, the project,
the status map, and where the plan + manifest live. Progress is recorded in the
config's `state_file` so a re-run resumes and never duplicates.

Identity is the canonical `<!-- syncline-id: KEY -->` body marker (AD-3/AD-4,
bin/syncline_markers.py) — the ONE identity mechanism. The old title-based
identity matcher was retired in Story 2.1; its sole surviving use is the one-shot
title bootstrap inside bin/syncline_cutover.py. The marker-based stamp-on-create
and match-by-key resolution land in Story 2.3; until then the write path below is
guarded (see main()) so it fails loud rather than silently duplicating.

Requires `gh` authed with the `project` scope (`gh auth login -s project`).

Usage:
  syncline_import.py --config <path>              # perform the import
  syncline_import.py --config <path> --dry-run    # resolve IDs + print plan only
"""
import argparse
import json
import os
import subprocess


def gh(*args, check=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}\n{r.stderr.strip()}")
    return r.returncode, r.stdout, r.stderr


def graphql(query, feature=None, **fields):
    """Run a GraphQL op. int/bool are sent typed (-F) so Int!/Boolean! vars work;
    everything else is a string (-f). A string that happens to look like a number
    stays a string via -f (we only auto-type real int/bool Python values)."""
    args = ["api", "graphql"]
    if feature:
        args += ["-H", f"GraphQL-Features: {feature}"]
    args += ["-f", f"query={query}"]
    for k, v in fields.items():
        flag = "-F" if isinstance(v, (int, bool)) and not isinstance(v, str) else "-f"
        args += [flag, f"{k}={v}"]
    code, out, err = gh(*args, check=False)
    data = json.loads(out) if out.strip() else {}
    if code != 0 or "errors" in data:
        raise RuntimeError(f"GraphQL error: {err.strip() or json.dumps(data.get('errors'))}")
    return data["data"]


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def resolve_project(owner, owner_type, project_name):
    """Find a ProjectV2 by title under a user or org owner. Returns its node id."""
    field = "organization" if owner_type == "org" else "user"
    data = graphql(
        f'query($o:String!){{ {field}(login:$o){{ projectsV2(first:50){{ nodes{{ id number title }} }} }} }}',
        o=owner,
    )
    nodes = data[field]["projectsV2"]["nodes"]
    proj = next((p for p in nodes if p["title"].strip().lower() == project_name.lower()), None)
    if not proj:
        raise SystemExit(
            f"Project {project_name!r} not found under {owner_type} {owner!r}. "
            f"Found: {[p['title'] for p in nodes]}"
        )
    return proj


def main():
    ap = argparse.ArgumentParser(description="Syncline importer")
    ap.add_argument("--config", required=True, help="path to a Syncline config JSON")
    ap.add_argument("--dry-run", action="store_true", help="resolve IDs + print plan, no writes")
    args = ap.parse_args()

    cfg = load_json(args.config, None)
    if cfg is None:
        raise SystemExit(f"Could not read config: {args.config}")

    cfg_dir = os.path.dirname(os.path.abspath(args.config))

    def rel(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg_dir, p))

    owner = cfg["repo_owner"]
    owner_type = cfg.get("owner_type", "org")  # "org" | "user"
    repo_name = cfg["repo_name"]
    project_name = cfg["project_name"]
    status_map = cfg["status_map"]            # raw plan status -> board option name
    type_field_name = cfg.get("type_field_name", "Work Type")
    manifest_path = rel(cfg["manifest_file"])

    manifest = load_json(manifest_path, None)
    if manifest is None:
        raise SystemExit(f"Could not read manifest: {manifest_path}")

    proj = resolve_project(owner, owner_type, project_name)
    project_id = proj["id"]
    print(f"Project: {proj['title']} (#{proj['number']}) under {owner_type} {owner}")

    def read_fields():
        nodes = graphql(
            "query($id:ID!){ node(id:$id){ ... on ProjectV2 { fields(first:50){ nodes{"
            " ... on ProjectV2FieldCommon { id name }"
            " ... on ProjectV2SingleSelectField { id name options{ id name } } } } } } }",
            id=project_id,
        )["node"]["fields"]["nodes"]
        return {f["name"]: f for f in nodes if f}

    fields = read_fields()
    if "Status" not in fields:
        raise SystemExit("Project has no 'Status' field.")
    status_field = fields["Status"]
    status_opt = {o["name"].lower(): o["id"] for o in status_field["options"]}
    print(f"Status options: {list(status_opt)}")

    # Create the Work Type field if absent (NOTE: "Type" is a RESERVED Projects v2
    # field name — a custom field cannot be called "Type"; default to "Work Type").
    type_field = fields.get(type_field_name)
    if not type_field and not args.dry_run:
        print(f"Creating custom {type_field_name!r} single-select field (Epic/Story)...")
        graphql(
            "mutation($p:ID!,$n:String!){ createProjectV2Field(input:{projectId:$p, dataType:SINGLE_SELECT, name:$n,"
            ' singleSelectOptions:['
            '{name:"Epic", color:PURPLE, description:"A plan epic"},'
            '{name:"Story", color:BLUE, description:"A story within an epic"}]}){ projectV2Field{ __typename } } }',
            p=project_id, n=type_field_name,
        )
        fields = read_fields()
        type_field = fields.get(type_field_name)
    type_opt = {o["name"].lower(): o["id"] for o in type_field["options"]} if type_field else {}

    if args.dry_run:
        # Dry-run still resolves the project + Status options + Work Type field
        # above (useful for config validation); it performs no writes. Printing a
        # full per-item plan from the new items[] manifest lands in Story 2.2.
        print("\n[dry-run] project + fields resolved; no writes performed.")
        return

    # --- WRITE PATH GUARD (Story 2.1): title-based identity is retired. ---
    #
    # The old write path matched existing issues BY TITLE (an `existing` dict
    # keyed on `title.strip()`) to stay idempotent. That title identity matcher
    # has been removed here as part of "one matcher, one funeral" (AC4, AD-4):
    # the ONE identity mechanism is now the canonical `<!-- syncline-id: KEY -->`
    # body marker (bin/syncline_markers.py). The old title-matcher's only
    # surviving use is the ONE-SHOT bootstrap inside bin/syncline_cutover.py,
    # which backfills that marker onto already-imported issues.
    #
    # Rewiring import to (a) consume the new items[] manifest shape, (b) stamp
    # the marker on create (FR5), and (c) resolve existing issues by marker/key
    # (FR6) is Story 2.3. Until that lands, running the real import would create
    # issues with NO identity marker and NO way to match existing ones — i.e. it
    # would silently DUPLICATE every issue. So we fail loud instead of silent.
    #
    # (Import has been non-runnable end-to-end since Story 1.1 anyway: the adapter
    # now emits items[] and no longer emits the manifest["epics"]/["stories"]
    # shape this file's old write path consumed. This guard makes that honest.)
    raise SystemExit(
        "syncline_import real import is disabled pending Story 2.3 "
        "(marker-based stamp-on-create + match-by-key over the new items[] manifest). "
        "The title-based identity matcher was retired in Story 2.1; running import now "
        "would create unmarked, unmatchable duplicate issues. "
        "To backfill identity markers onto an already-imported board, run "
        "bin/syncline_cutover.py. Use --dry-run here to resolve/validate the board only."
    )


if __name__ == "__main__":
    main()
