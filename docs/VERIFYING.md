# Verifying Syncline (manual, against a disposable scratch board)

Syncline's import and sync write to a **real** GitHub Projects v2 board and
**mutate issues destructively** (create, close, reopen, link). v1 ships with
**no CI** — there is no automated gate. Verification is **manual, against a
throwaway scratch board you create and delete**, and that is stated here plainly
rather than dressed up as automated testing.

The rule is simple: **develop and verify every destructive change against a
disposable scratch board first — never against a real project.**

What this document is *not*: it does not claim Syncline is correct on an
arbitrary board. Correctness is promised only on **well-formed inputs and a
correctly-configured board** (see the honest-limits notes in the README).

> **Target vs. today.** This checklist describes the **complete** import/sync
> behaviour as the engine reaches it across Epic 2–3. Several boxes
> (identity marker, match-by-key, richer `--dry-run` output, cycle/cap
> validation) are **not true yet** on an unmigrated engine — they land in
> Stories 2.1–2.6. Each box that depends on later work is tagged
> **`[lands: 2.x]`**. A story is "verified on the scratch board" when the boxes
> up to and including its own tag pass. If a tagged box fails before its story,
> that is expected, not a bug.

---

## 1. Prerequisites

```bash
gh auth status        # must list the 'project' scope
```

If `project` is missing, authenticate (interactive — an agent cannot do this
for you):

```bash
gh auth login -s project -h github.com
```

A plain `repo`-scoped token returns `INSUFFICIENT_SCOPES` on every Projects v2
call. `gh` must also be able to create issues in the repo you point the config
at — use a throwaway or personal repo you own, never a shared/production one.

---

## 2. Create the scratch board

A Projects v2 board is a **live, `project`-scoped object**, not a checked-in
fixture — so you create a real one and tear it down afterwards.

```bash
# Create a disposable board under your own account (owner_type: user).
gh project create --owner "@me" --title "Syncline Scratch Board" --format json
```

The JSON output includes the board's **`number`**, node **`id`** (`PVT_…`), and
`url`. You need both, for different things:

- the **`number`** → goes in the config (`project_name` is matched by title, but
  `number` is what you pass to `gh project` commands) and in teardown;
- the node **`id`** (`PVT_…`) → goes in the GraphQL field-inspection query below.

Abridged example output (real output has more fields):

```json
{ "number": 4, "id": "PVT_kwABC…", "url": "https://github.com/users/<you>/projects/4",
  "title": "Syncline Scratch Board", "owner": { "login": "<you>", "type": "User" } }
```

### Status options → sample `status_map`

A freshly created board has the default single-select **Status** options
**`Todo` / `In Progress` / `Done`**. GitHub's GraphQL API has no stable
mutation to add options to the built-in Status field, so the honest approach is
to point a *sample* `status_map` at the three options that already exist — which
also demonstrates the whole point of `status_map`: it decouples your plan's raw
status vocabulary from the board's option names. Option names are matched
**case-insensitively** by the engine, so `"Done"` and `"done"` are equivalent.

Inspect the options with the node **id** from the create output:

```bash
gh api graphql -f query='query($id:ID!){ node(id:$id){ ... on ProjectV2 {
  fields(first:20){ nodes{
    ... on ProjectV2SingleSelectField{ name options{ id name } } } } } } }' \
  -f id='PVT_kwABC…'   # the node id, NOT the number
```

### The scratch config + a minimal sample plan

`*_file` paths in a config resolve **relative to that config file's own
directory** (absolute paths are used as-is). The scratch config lives at
`configs/scratch.config.json`, so the paths below resolve under `configs/`.
Create the dirs and sample plan first:

```bash
mkdir -p configs/scratch configs/state
```

`configs/scratch/epics.md` — a tiny two-epic plan:

```markdown
## Epic 1: First Epic
## Epic 2: Second Epic
```

`configs/scratch/sprint-status.yaml` — statuses spanning the map, including one `done`:

```yaml
development_status:
  epic-1: in-progress
  1-1-alpha: done
  1-2-beta: in-progress
  epic-2: backlog
  2-1-gamma: backlog
```

`configs/scratch.config.json` — targets the board's default Status options:

```json
{
  "project_name": "Syncline Scratch Board",
  "repo_owner": "<you>", "owner_type": "user", "repo_name": "<throwaway-repo>",
  "status_map": {
    "backlog": "Todo",
    "ready-for-dev": "Todo",
    "in-progress": "In Progress",
    "review": "In Progress",
    "done": "Done"
  },
  "done_statuses": ["done"],
  "type_field_name": "Work Type",
  "plan_file": "./scratch/sprint-status.yaml",
  "epics_file": "./scratch/epics.md",
  "manifest_file": "./state/scratch.manifest.json",
  "state_file": "./state/scratch.import-state.json",
  "cache_file": "./state/scratch.sync-cache.json",
  "label": "scratch"
}
```

See `configs/example.config.json` for what every field means. (`./scratch/…`
and `./state/…` resolve to `configs/scratch/…` and `configs/state/…` — the
config's own directory, per the rule above.)

---

## 3. The manual-verification checklist

Run the full cycle below against the scratch board. Each per-story spec in
Epics 2–3 carries a **"verified on scratch board"** acceptance line that this
checklist satisfies — tick the boxes up to that story's `[lands: 2.x]` tag.

### a. Build the manifest (no GitHub)

```bash
python3 bin/syncline_build_manifest.py --config configs/scratch.config.json --print | head -40
```

- [ ] Output is `{ schema_version: 1, project, repo, status_map, items[] }`.
- [ ] Every item is `{ key, parent, type, title, status }`; epics have
      `parent: null`, stories point `parent: "epic-N"`.

### b. Dry-run the import (validation only, no writes)

```bash
python3 bin/syncline_import.py --config configs/scratch.config.json --dry-run
```

- [ ] The project resolves (right owner/type/name) and its Status options are read.
- [ ] The plan prints and **no issues are created** (check the repo).
- [ ] `[lands: 2.2]` The full plan prints every item with its mapped Status,
      Work Type, and parent.
- [ ] `[lands: 2.2]` A misconfiguration **fails legibly, names the offender, and
      writes nothing**. Negative test: temporarily set a plan line's status to
      one absent from `status_map` (e.g. `1-1-alpha: nope`) and re-run — confirm
      it names `1-1-alpha` and exits non-zero. (The adapter already enforces
      status/key-grammar/uniqueness in Story 1.1; board-option coverage,
      parent-resolution, cycle, and the 100-child / 8-level cap checks land in
      Story 2.2.)

### c. Import for real

```bash
python3 bin/syncline_import.py --config configs/scratch.config.json
```

Get the two counts to compare:

```bash
python3 bin/syncline_build_manifest.py --config configs/scratch.config.json   # prints "N epics, M stories"
gh project item-list 4 --owner "@me" --format json | python3 -c "import json,sys; print('board items:', json.load(sys.stdin)['totalCount'])"
```

- [ ] **Counts:** board items == epics + stories in the manifest.
- [ ] **Status:** each item's board Status matches its mapped `status_map` value.
- [ ] **Work Type:** each item is tagged `Epic` or `Story` (field named exactly
      `Work Type`, created if absent — never `Type`).
- [ ] `[lands: 2.3]` **Identity:** each created issue's body ends with its
      own-line `<!-- syncline-id: KEY -->` marker. Check one:
      `gh issue view <number> --repo <you>/<repo> --json body -q .body | tail -1`
- [ ] `[lands: 2.5]` **Hierarchy:** each story is a native sub-issue of its
      epic; no orphans. Check with:
      ```bash
      gh api graphql -f query='query($o:String!,$n:String!){ repository(owner:$o,name:$n){
        issues(first:100,states:[OPEN,CLOSED]){ nodes{ number title
          subIssues(first:30){ totalCount } parent{ number } } } } }' \
        -f o='<you>' -f n='<throwaway-repo>'
      ```
- [ ] `[lands: 2.6]` **Open/closed:** items whose status is in `done_statuses`
      are **closed**; all others **open**. Check with:
      `gh issue list --repo <you>/<repo> --state all --json number,title,state`

### d. Idempotency & resume

```bash
python3 bin/syncline_import.py --config configs/scratch.config.json   # run again
```

- [ ] A second run creates **no duplicates**.
- [ ] `[lands: 2.3]` Rename an issue's **title** on the board, re-run → still
      matched by marker/key (not title), **not** duplicated.
- [ ] `[lands: 2.3]` If you interrupt a run mid-import, re-running **resumes**
      (state saved per item) and produces no duplicates.

### e. Edit the plan, then sync

Change a status in `configs/scratch/sprint-status.yaml` (e.g. `1-2-beta`
`in-progress` → `done`), then:

```bash
python3 bin/syncline_sync.py --config configs/scratch.config.json
```

- [ ] Only the changed item is reconciled: its Status updates **and** its issue
      closes/reopens in the same step.
- [ ] Add a brand-new plan item → sync creates, places (Status + Work Type), and
      links it under its parent.
- [ ] Re-run with **no plan change** → **silent no-op**, no GitHub writes
      (sha256 cache).
- [ ] Sync prints **one** concise line only when it changed something.

---

## 4. Teardown

Delete the scratch board when you are done (it is disposable — confirm the
number first, this is irreversible):

```bash
gh project delete 4 --owner "@me"       # use YOUR board number
```

Then remove the local scratch artifacts and the throwaway repo's test issues:

```bash
rm -f configs/state/scratch.*           # manifest / import-state / sync-cache
# close/delete the issues the import created in <throwaway-repo> if you want it clean
```

The scratch board, state, manifest, and cache are all **local-only /
throwaway** — they never travel to teammates or CI.
