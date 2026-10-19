#!/usr/bin/env python3
"""
Syncline — incremental sync (the hook-called reconciler).

Reconciles a plan's epic/story statuses onto a GitHub Project board: sets each
item's Status (via the config status_map) and the issue open/closed state, and
creates+links any plan item not yet on the board. Designed to run on EVERY Claude
Code Stop (or on demand) and be invisible unless something actually changed.

Hard guarantees (so it is safe on every Stop):
  - NO-OP + exit 0 when: gh is missing/unauthed, the state file is absent (nothing
    imported yet), or the plan file is unchanged since the last successful sync
    (sha256-cached).
  - NEVER blocks or errors the caller: every path is wrapped; the process ALWAYS
    exits 0 and prints at most one concise line (the Stop hook surfaces it).

Config-driven (see configs/example.config.json). Nothing is hard-coded to a repo.

Requires `gh` authed with the `project` scope.

Usage:
  syncline_sync.py --config <path>
  (A Stop hook typically runs: python3 .../syncline_sync.py --config <path> </dev/null 2>/dev/null || true)
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys


def gh_ok():
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def gql(query, **fvars):
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in fvars.items():
        # Int!/Boolean! GraphQL vars need -F (typed); -f would stringify and the
        # API rejects a string for `$num: Int!`. Only real int/bool auto-type.
        flag = "-F" if isinstance(v, (int, bool)) and not isinstance(v, str) else "-f"
        args += [flag, f"{k}={v}"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    data = json.loads(r.stdout) if r.stdout.strip() else {}
    if r.returncode != 0 or "errors" in data:
        raise RuntimeError(r.stderr.strip() or json.dumps(data.get("errors")))
    return data["data"]


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def parse_plan(plan_path, epic_re, story_re, valid_statuses):
    """Return {key -> raw status} for epic lines (epic_re) and story lines (story_re)."""
    out = {}
    try:
        with open(plan_path) as fh:
            lines = fh.readlines()
    except Exception:
        return out
    for line in lines:
        for rx in (epic_re, story_re):
            m = rx.match(line)
            if m and m.group(2) in valid_statuses:
                out[m.group(1)] = m.group(2)
                break
    return out


def main():
    ap = argparse.ArgumentParser(description="Syncline incremental sync")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg = load(args.config, None)
    if cfg is None:
        return  # unreadable config -> silent no-op

    cfg_dir = os.path.dirname(os.path.abspath(args.config))

    def rel(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg_dir, p))

    owner = cfg["repo_owner"]
    owner_type = cfg.get("owner_type", "org")
    repo_name = cfg["repo_name"]
    project_name = cfg["project_name"]
    status_map = cfg["status_map"]
    done_statuses = set(cfg.get("done_statuses", ["done"]))
    type_field_name = cfg.get("type_field_name", "Work Type")
    plan_path = rel(cfg["plan_file"])
    state_path = rel(cfg["state_file"])
    cache_path = rel(cfg["cache_file"])
    manifest_path = rel(cfg.get("manifest_file", "")) if cfg.get("manifest_file") else None
    label = cfg.get("label", project_name)
    # Line patterns: group(1)=key, group(2)=status. Overridable per project.
    epic_re = re.compile(cfg.get("epic_line_regex", r"^\s{2}(epic-\d+):\s*(\S+)"))
    story_re = re.compile(cfg.get("story_line_regex", r"^\s{2}(\d+-\d+-[a-z0-9-]+):\s*(\S+)"))
    story_key_re = re.compile(cfg.get("story_key_regex", r"^\d+-\d+-"))
    epic_num_re = re.compile(cfg.get("epic_num_from_story_regex", r"^(\d+)-"))

    if not os.path.exists(plan_path) or not os.path.exists(state_path):
        return  # nothing imported yet -> silent no-op
    if not gh_ok():
        return  # not authed -> silent no-op

    cache = load(cache_path, {})
    plan_hash = hashlib.sha256(open(plan_path, "rb").read()).hexdigest()
    if cache.get("plan_hash") == plan_hash:
        return  # unchanged since last successful sync -> no-op

    state = load(state_path, {"epics": {}, "stories": {}})
    plan = parse_plan(plan_path, epic_re, story_re, set(status_map.keys()))

    # Resolve + cache project/field ids once.
    pid = cache.get("project_id")
    sfield = cache.get("status_field")
    sopts = cache.get("status_opts")
    if not (pid and sfield and sopts):
        field = "organization" if owner_type == "org" else "user"
        nodes = gql(
            f"query($o:String!){{ {field}(login:$o){{ projectsV2(first:50){{ nodes{{ id number title }} }} }} }}",
            o=owner,
        )[field]["projectsV2"]["nodes"]
        proj = next((p for p in nodes if p["title"].strip().lower() == project_name.lower()), None)
        if not proj:
            return
        pid = proj["id"]
        f = gql(
            "query($id:ID!){ node(id:$id){ ... on ProjectV2 { fields(first:50){ nodes{"
            " ... on ProjectV2FieldCommon{ id name }"
            " ... on ProjectV2SingleSelectField{ id name options{ id name } } } } } } }",
            id=pid,
        )["node"]["fields"]["nodes"]
        fmap = {x["name"]: x for x in f if x}
        if "Status" not in fmap:
            return
        sfield = fmap["Status"]["id"]
        sopts = {o["name"].lower(): o["id"] for o in fmap["Status"]["options"]}
        cache.update({"project_id": pid, "status_field": sfield, "status_opts": sopts})
        if type_field_name in fmap:
            cache["type_field"] = fmap[type_field_name]["id"]
            cache["type_opts"] = {o["name"].lower(): o["id"] for o in fmap[type_field_name]["options"]}

    last = cache.get("last_status", {})
    changed = []

    def set_status(item_id, raw):
        name = status_map.get(raw)
        opt = sopts.get(name.lower()) if name else None
        if not opt:
            return
        gql(
            "mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{"
            " projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){ projectV2Item{ id } } }",
            p=pid, i=item_id, f=sfield, o=opt,
        )

    def issue_node(number):
        return gql(
            "query($o:String!,$n:String!,$num:Int!){ repository(owner:$o,name:$n){ issue(number:$num){ id state } } }",
            o=owner, n=repo_name, num=number,
        )["repository"]["issue"]

    def set_issue_state(number, want_closed):
        # Retry once on a transient hiccup so board Status and issue open/closed
        # never drift apart.
        for attempt in range(2):
            try:
                info = issue_node(number)
                is_closed = info["state"] == "CLOSED"
                if want_closed and not is_closed:
                    gql("mutation($i:ID!){ closeIssue(input:{issueId:$i}){ issue{ number } } }", i=info["id"])
                elif not want_closed and is_closed:
                    gql("mutation($i:ID!){ reopenIssue(input:{issueId:$i}){ issue{ number } } }", i=info["id"])
                return
            except Exception:
                if attempt == 1:
                    raise

    def reconcile(key, rec):
        raw = plan.get(key)
        if raw is None or last.get(key) == raw:
            return
        set_status(rec["item"], raw)
        set_issue_state(rec["issue"], raw in done_statuses)
        last[key] = raw
        changed.append(key)

    for ekey, rec in state.get("epics", {}).items():
        reconcile(f"epic-{ekey}", rec)
    for skey, rec in state.get("stories", {}).items():
        reconcile(skey, rec)

    # New plan items not yet on the board: create + add + status + type + link.
    known_stories = set(state.get("stories", {}).keys())
    known_epics = set(state.get("epics", {}).keys())
    new_keys = [
        k for k in plan
        if (story_key_re.match(k) and k not in known_stories)
        or (k.startswith("epic-") and k[5:] not in known_epics)
    ]
    if new_keys:
        repo_id = gql(
            "query($o:String!,$n:String!){ repository(owner:$o,name:$n){ id } }",
            o=owner, n=repo_name,
        )["repository"]["id"]
        manifest = load(manifest_path, {"stories": [], "epics": []}) if manifest_path else {"stories": [], "epics": []}
        stitle = {s["key"]: s["title"] for s in manifest.get("stories", [])}
        etitle = {e["epic"]: e["title"] for e in manifest.get("epics", [])}

        def ensure(title, body):
            d = gql(
                "mutation($r:ID!,$t:String!,$b:String!){ createIssue(input:{repositoryId:$r,title:$t,body:$b}){ issue{ id number } } }",
                r=repo_id, t=title, b=body,
            )["createIssue"]["issue"]
            item = gql(
                "mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c}){ item{ id } } }",
                p=pid, c=d["id"],
            )["addProjectV2ItemById"]["item"]["id"]
            return d["id"], d["number"], item

        def set_type(item, which):
            tf, to = cache.get("type_field"), cache.get("type_opts", {})
            if tf and to.get(which):
                gql(
                    "mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){ projectV2Item{ id } } }",
                    p=pid, i=item, f=tf, o=to[which],
                )

        for k in new_keys:
            raw = plan[k]
            if k.startswith("epic-"):
                n = int(k[5:])
                title = etitle.get(n, f"Epic {n}")
                nid, num, item = ensure(title, "Imported by Syncline.")
                set_status(item, raw)
                set_type(item, "epic")
                set_issue_state(num, raw in done_statuses)
                state.setdefault("epics", {})[str(n)] = {"issue": num, "item": item}
            else:
                title = stitle.get(k) or k
                em = epic_num_re.match(k)
                epic_n = int(em.group(1)) if em else None
                nid, num, item = ensure(title, f"Imported by Syncline. Key: {k}.")
                set_status(item, raw)
                set_type(item, "story")
                parent = state.get("epics", {}).get(str(epic_n)) if epic_n is not None else None
                if parent:
                    try:
                        gql(
                            "mutation($p:ID!,$c:ID!){ addSubIssue(input:{issueId:$p,subIssueId:$c}){ subIssue{ number } } }",
                            p=issue_node(parent["issue"])["id"], c=nid,
                        )
                    except Exception:
                        pass
                state.setdefault("stories", {})[k] = {"issue": num, "item": item, "epic": epic_n}
            last[k] = raw
            changed.append(k)
        with open(state_path, "w") as fh:
            json.dump(state, fh, indent=2)

    cache["plan_hash"] = plan_hash
    cache["last_status"] = last
    with open(cache_path, "w") as fh:
        json.dump(cache, fh, indent=2)
    if changed:
        print(f"[{label}] project synced: {len(changed)} item(s) updated on the board.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never surface a failure into the caller (the Stop hook)
    sys.exit(0)
