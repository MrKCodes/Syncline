#!/usr/bin/env python3
"""
Syncline — importer.

One-time (idempotent, resumable) import of a plan's epics + stories into a GitHub
Project (v2) as real repo issues, with native epic→story sub-issue links, a
single-select Status, and a custom single-select "Work Type" (Epic/Story) field.

Config-driven: nothing here is hard-coded to a particular repo. Point it at a
config JSON (see configs/example.config.json) describing the repo, the project,
the status map, and where the plan + manifest live. Progress is recorded in the
config's `state_file` so a re-run resumes and never duplicates (issues are matched
by exact title).

Requires `gh` authed with the `project` scope (`gh auth login -s project`).

Usage:
  syncline_import.py --config <path>              # perform the import
  syncline_import.py --config <path> --dry-run    # resolve IDs + print plan only
"""
import argparse
import json
import os
import subprocess
import sys


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
    done_statuses = set(cfg.get("done_statuses", ["done"]))  # which raw statuses close the issue
    type_field_name = cfg.get("type_field_name", "Work Type")
    manifest_path = rel(cfg["manifest_file"])
    state_path = rel(cfg["state_file"])

    manifest = load_json(manifest_path, None)
    if manifest is None:
        raise SystemExit(f"Could not read manifest: {manifest_path}")

    state = load_json(state_path, {"epics": {}, "stories": {}})

    def save_state():
        with open(state_path, "w") as fh:
            json.dump(state, fh, indent=2)

    proj = resolve_project(owner, owner_type, project_name)
    project_id = proj["id"]
    print(f"Project: {proj['title']} (#{proj['number']}) under {owner_type} {owner}")

    repo_id = graphql(
        "query($o:String!,$n:String!){ repository(owner:$o,name:$n){ id } }",
        o=owner, n=repo_name,
    )["repository"]["id"]

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

    def mapped(status):
        name = status_map.get(status)
        if not name:
            raise SystemExit(f"Status {status!r} is not in the config status_map.")
        opt = status_opt.get(name.lower())
        if not opt:
            raise SystemExit(f"Board has no Status option {name!r} (needed for raw {status!r}).")
        return opt

    print("\n--- PLAN ---")
    for e in manifest["epics"]:
        print(f"  EPIC  {e['title']}  -> {status_map.get(e['status'], e['status'])}")
    print(f"  + {len(manifest['stories'])} stories as sub-issues")
    if args.dry_run:
        print("\n[dry-run] no writes performed.")
        return

    # Existing issues (title -> node) for idempotency.
    existing = {}
    after = None
    while True:
        page = graphql(
            "query($o:String!,$n:String!,$after:String){ repository(owner:$o,name:$n){"
            " issues(first:100, after:$after, states:[OPEN,CLOSED]){ pageInfo{ hasNextPage endCursor }"
            " nodes{ id number title state } } } }",
            o=owner, n=repo_name, **({"after": after} if after else {}),
        )["repository"]["issues"]
        for n in page["nodes"]:
            existing[n["title"].strip()] = n
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    print(f"Existing repo issues scanned: {len(existing)}")

    def ensure_issue(title, body):
        if title in existing:
            n = existing[title]
            return {"id": n["id"], "number": n["number"]}
        d = graphql(
            "mutation($r:ID!,$t:String!,$b:String!){ createIssue(input:{repositoryId:$r,title:$t,body:$b}){"
            " issue{ id number } } }",
            r=repo_id, t=title, b=body,
        )["createIssue"]["issue"]
        existing[title] = {"id": d["id"], "number": d["number"], "title": title, "state": "OPEN"}
        print(f"  + issue #{d['number']}  {title}")
        return d

    def add_to_project(content_id):
        return graphql(
            "mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c}){ item{ id } } }",
            p=project_id, c=content_id,
        )["addProjectV2ItemById"]["item"]["id"]

    def set_single_select(item_id, field_id, option_id):
        graphql(
            "mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{"
            " projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){ projectV2Item{ id } } }",
            p=project_id, i=item_id, f=field_id, o=option_id,
        )

    def link_sub_issue(parent_id, child_id):
        q = "mutation($p:ID!,$c:ID!){ addSubIssue(input:{issueId:$p, subIssueId:$c}){ subIssue{ number } } }"
        try:
            graphql(q, p=parent_id, c=child_id)
        except RuntimeError as e:
            if "already" in str(e).lower():
                return
            graphql(q, feature="sub_issues", p=parent_id, c=child_id)  # preview-header fallback

    def close_issue(issue_id):
        graphql("mutation($i:ID!){ closeIssue(input:{issueId:$i}){ issue{ number } } }", i=issue_id)

    # Phase 1: epics.
    epic_issue = {}
    for e in manifest["epics"]:
        body = (
            f"**Plan epic** ({e.get('story_count', '?')} stories).\n\n"
            f"Rollup status: **{status_map.get(e['status'], e['status'])}**.\n\n"
            f"_Imported by Syncline from the plan._"
        )
        iss = ensure_issue(e["title"], body)
        epic_issue[e["epic"]] = iss
        item = add_to_project(iss["id"])
        set_single_select(item, status_field["id"], mapped(e["status"]))
        if type_opt.get("epic"):
            set_single_select(item, type_field["id"], type_opt["epic"])
        state["epics"][str(e["epic"])] = {"issue": iss["number"], "item": item}
        save_state()

    # Phase 2: stories (create -> project -> status/type -> sub-issue link).
    for s in manifest["stories"]:
        body = (
            f"Story **{s['story']}** of Epic **{s['epic']}**.  Key: `{s['key']}`.\n\n"
            f"Status: **{status_map.get(s['status'], s['status'])}**.\n\n"
            f"_Imported by Syncline from the plan._"
        )
        iss = ensure_issue(s["title"], body)
        item = add_to_project(iss["id"])
        set_single_select(item, status_field["id"], mapped(s["status"]))
        if type_opt.get("story"):
            set_single_select(item, type_field["id"], type_opt["story"])
        parent = epic_issue.get(s["epic"])
        if parent:
            link_sub_issue(parent["id"], iss["id"])
        state["stories"][s["key"]] = {"issue": iss["number"], "item": item, "epic": s["epic"]}
        save_state()

    # Phase 3: close issues whose status is terminal (keeps the tracker tidy).
    for e in manifest["epics"]:
        if e["status"] in done_statuses:
            close_issue(epic_issue[e["epic"]]["id"])
    for s in manifest["stories"]:
        if s["status"] in done_statuses:
            close_issue(existing[s["title"]]["id"])

    print(
        f"\nDone. {len(epic_issue)} epics + {len(manifest['stories'])} stories on the board; "
        f"stories linked as sub-issues; terminal items closed."
    )
    save_state()


if __name__ == "__main__":
    main()
