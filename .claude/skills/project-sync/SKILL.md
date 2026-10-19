---
name: project-sync
description: Mirror a BMAD-style plan (epics/stories/sprint status) to a GitHub Project (v2) board — one-time import as real issues + sub-issues, and keep statuses in sync. Use when the user asks to "sync the project board", "import epics/stories into GitHub Projects", "set up the board", "update the project tasks", or wire an auto-sync hook. This is the Syncline support tooling.
---

# Syncline — plan ↔ GitHub Project sync

Syncline mirrors a BMAD-style plan (epics, stories, sprint status) onto a GitHub
**Project v2** board: epics and stories become **real repo issues** with native
**sub-issue** links (epic → its stories), a single-select **Status**, and a custom
**Work Type** (Epic/Story) field. One importer seeds the board; one sync keeps it
current and is safe to run on every Claude Code Stop.

Scripts live in `bin/` next to this project; configs in `configs/`. Everything is
**config-driven** — one config JSON per repo, nothing hard-coded.

## Prerequisites (check first)

1. `gh` CLI installed AND authed with the **`project` scope**:
   `gh auth status` should list `project` in the scopes. If not:
   `gh auth login -s project -h github.com` (the user must run this; you cannot do
   interactive auth). A plain push token has `repo` but NOT `project`, and Projects
   v2 is GraphQL-only — without `project` scope every call returns INSUFFICIENT_SCOPES.
2. `python3` on PATH.
3. The target GitHub Project (v2) already exists (note its owner + owner type:
   org vs user) and has a **Status** single-select field with the option names
   your config's `status_map` points at.

## Config

Copy `configs/example.config.json` to `configs/<name>.config.json` and fill it in.
Every field is documented inline there. Key points:
- `*_file` paths resolve **relative to the config file's directory** (or pass
  absolute). Point `plan_file`/`epics_file`/`story_spec_glob` at the target repo's
  BMAD artifacts; keep `manifest_file`/`state_file`/`cache_file` under `state/`
  here (create `configs/state/` — it is Syncline's own bookkeeping, local-only).
- `owner_type` is `"org"` or `"user"` — get it from
  `gh api repos/<owner>/<repo> --jq .owner.type`.
- `status_map` maps raw plan statuses → exact board option names.

## Workflow

### 1. Build the manifest (BMAD layout)
```
python3 bin/syncline_build_manifest.py --config configs/<name>.config.json
```
Reads `epics.md` (titles) + `sprint-status.yaml` (status) + optional spec glob
(nicer story titles) → writes `manifest_file`. Re-run whenever epics/stories are
added. (A non-BMAD project can hand-write the manifest JSON; shape is documented
in `syncline_build_manifest.py`.)

### 2. Dry-run the import (verify the board + plan before any writes)
```
python3 bin/syncline_import.py --config configs/<name>.config.json --dry-run
```
Confirms the project resolves and prints what it would create. ALWAYS dry-run first.

### 3. Import (idempotent, resumable)
```
python3 bin/syncline_import.py --config configs/<name>.config.json
```
Creates issues (matched by title → never duplicated), adds them to the board, sets
Status + Work Type, links stories as sub-issues of their epic, and closes
terminal-status issues. Records progress in `state_file`; safe to re-run.

### 4. Sync (ongoing — and the hook)
```
python3 bin/syncline_sync.py --config configs/<name>.config.json
```
Reconciles changed statuses + issue open/closed, and creates+links any new plan
item. No-ops (exit 0, silent) when gh is unauthed, nothing is imported, or the plan
is unchanged (sha256-cached). NEVER throws into its caller.

To auto-sync on every Claude Code Stop, add a Stop hook to the TARGET repo's
`.claude/settings.json` (see `docs/HOOK.md`):
```json
{ "hooks": { "Stop": [ { "hooks": [ {
  "type": "command",
  "command": "python3 /ABS/PATH/Syncline/bin/syncline_sync.py --config /ABS/PATH/Syncline/configs/<name>.config.json </dev/null 2>/dev/null || true",
  "timeout": 90,
  "statusMessage": "Syncing plan to the project board…"
} ] } ] } }
```

## Hard-won gotchas (these are real, learned the hard way)

- **GraphQL `Int!` typing.** `gh api graphql -f num=5` sends a STRING and the API
  rejects it for `$num: Int!` ("Variable $num of type Int! was provided invalid
  value"). The scripts send real int/bool via `-F`. If you add a query taking an
  Int/Boolean var, pass a real Python int/bool so the `-F` path fires.
- **"Type" is reserved.** You cannot create a Projects v2 field named `Type`
  ("Name has already been taken / reserved value"). Syncline uses `Work Type`.
- **`project` scope is mandatory** (see Prerequisites). The symptom is
  INSUFFICIENT_SCOPES on every GraphQL call.
- **Keep board ↔ plan the single source of truth, and verify the rollup.** After
  marking a story done in the plan, confirm the epic's children all match the epic
  state — a silent string-replace that doesn't land leaves a child stuck (e.g. an
  epic shows Done while one story shows Backlog). After a sync, spot-check with the
  verify snippet in `docs/USAGE.md`.
- **State is local-only.** `manifest/state/cache` are Syncline's bookkeeping, not
  the repo's. If the target repo gitignores its plan dir, these live here under
  `configs/state/` and do not travel to teammates/CI — intended for a personal
  workflow; call it out if the board sync should be team-wide.
- **Sub-issues:** `addSubIssue` is GA; the importer retries with a
  `GraphQL-Features: sub_issues` preview header if a call is rejected.

## When to use which script

| Goal | Script |
| --- | --- |
| First-time board population | `syncline_import.py` (dry-run, then real) |
| Keep statuses current / add new items | `syncline_sync.py` (also the hook) |
| Regenerate the manifest after plan edits | `syncline_build_manifest.py` |

See `docs/USAGE.md` for a full walk-through and verification snippets, and
`docs/HOOK.md` for wiring the Stop hook.
