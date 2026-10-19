# Syncline — usage walk-through

End-to-end, with the verification snippets worth running at each step. Replace
`<cfg>` with your config path (e.g. `configs/subby.config.json`) and `OWNER`/`#N`
with your project's owner and number.

## 0. Preconditions

```bash
gh auth status                                   # must list the 'project' scope
gh api repos/OWNER/REPO --jq .owner.type         # "Organization" | "User" -> owner_type org|user
```
If `project` is missing: `gh auth login -s project -h github.com` (you run this;
an agent cannot do interactive auth).

List the target project + confirm its Status options exist and match `status_map`:
```bash
gh api graphql -f query='query($o:String!){ organization(login:"OWNER"){ projectsV2(first:50){ nodes{ number title } } } }' -f o=OWNER
```

## 1. Build the manifest

```bash
python3 bin/syncline_build_manifest.py --config <cfg>
# or inspect without writing:
python3 bin/syncline_build_manifest.py --config <cfg> --print | head -40
```
Prints the epic rollups so you can sanity-check (an epic is `done` only if every
story is terminal). Re-run after adding epics/stories to the plan.

## 2. Dry-run the import

```bash
python3 bin/syncline_import.py --config <cfg> --dry-run
```
Confirms the project resolves (right owner/type/name), reads its Status options,
and prints the plan. No writes. If the project isn't found, it lists the titles it
did see — usually an `owner_type` or `project_name` typo.

## 3. Import

```bash
python3 bin/syncline_import.py --config <cfg>
```
Idempotent (issues matched by exact title) and resumable (progress saved to
`state_file` after each item). Creates issues → adds to board → sets Status +
Work Type → links sub-issues → closes terminal items.

### Verify the import
```bash
# Item counts + Status/Work Type breakdown on the board:
gh api graphql -f query='query{ organization(login:"OWNER"){ projectV2(number:N){ items(first:100){ totalCount nodes{
  content{ ... on Issue { state } }
  fieldValues(first:20){ nodes{ ... on ProjectV2ItemFieldSingleSelectValue { name field{ ... on ProjectV2FieldCommon{ name } } } } }
} } } } }' | python3 -c "import json,sys;from collections import Counter;d=json.load(sys.stdin)['data']['organization']['projectV2']['items'];st=Counter();wt=Counter();iss=Counter()
for n in d['nodes']:
  c=n.get('content') or {};
  (iss.update([c['state']]) if c.get('state') else None)
  for fv in n['fieldValues']['nodes']:
    fn=(fv.get('field') or {}).get('name')
    (st.update([fv['name']]) if fn=='Status' else None); (wt.update([fv['name']]) if fn=='Work Type' else None)
print('items',d['totalCount'],'| Status',dict(st),'| Work Type',dict(wt),'| issues',dict(iss))"

# Sub-issue links per epic (epic -> its stories):
gh api graphql -f query='query{ repository(owner:"OWNER",name:"REPO"){ issues(first:100,states:[OPEN,CLOSED]){ nodes{ number title subIssues(first:30){ totalCount } parent{ number } } } } }' | python3 -c "import json,sys,re;d=json.load(sys.stdin)['data']['repository']['issues']['nodes']
for e in [n for n in d if n['title'].startswith('Epic ')]: print('EPIC',e['number'],'subs',e['subIssues']['totalCount'],e['title'])"
```

## 4. Sync (ongoing)

```bash
python3 bin/syncline_sync.py --config <cfg>
```
- Unchanged plan → silent no-op (sha256 cache).
- Changed statuses → updates Status + issue open/closed.
- New plan items → creates + adds + types + sub-issue-links them.
- Prints one line (`[label] project synced: N item(s) updated`) only when it changed something.

Wire it to run automatically on every Claude Code Stop — see `HOOK.md`.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `INSUFFICIENT_SCOPES` on every call | token lacks `project` scope → `gh auth login -s project` |
| `Variable $num of type Int! was provided invalid value` | an Int/Boolean GraphQL var was sent with `-f` (string) → use `-F` / pass a real int |
| `Name has already been taken` creating the type field | field named `Type` (reserved) → use `Work Type` (default) |
| Project not found (dry-run lists others) | wrong `owner_type` (org vs user) or `project_name` |
| A child item stuck in Backlog while its epic shows Done | a plan status edit didn't land before sync → fix the plan line, re-run sync, verify the rollup |
| Sync did "nothing" after a plan edit | the sha256 cache matched → confirm you edited `plan_file`; delete `cache_file` to force a full reconcile |
