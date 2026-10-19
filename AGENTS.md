# AGENTS.md — Syncline

Dev-support tooling, extracted from the Subby build so it can be reused across any
repo. The flagship tool is **Syncline**: it mirrors a BMAD-style plan
(epics / stories / sprint status) onto a **GitHub Project (v2)** board — a one-time
import of real issues + sub-issues, plus an incremental sync that keeps statuses
current and can run on every Claude Code Stop.

## Orient fast

- **What it does + how to run it:** `.claude/skills/project-sync/SKILL.md` is the
  authoritative operating guide (prerequisites, config, workflow, gotchas). Read it
  first — if the user asks to sync/import/set-up a project board, that skill drives
  the work.
- **Scripts:** `bin/`
  - `syncline_build_manifest.py` — build the epics/stories manifest from a BMAD
    `epics.md` + `sprint-status.yaml`.
  - `syncline_import.py` — one-time, idempotent, resumable board import (dry-run supported).
  - `syncline_sync.py` — incremental reconcile; the Stop-hook entry point; always exits 0.
- **Config:** `configs/example.config.json` (fully documented inline). One config
  JSON per repo; copy + fill. `configs/state/` holds Syncline's own bookkeeping
  (manifest / import-state / sync-cache) — local-only, gitignored.
- **Docs:** `docs/USAGE.md` (end-to-end walk-through + verify snippets),
  `docs/HOOK.md` (wiring the Stop hook into a target repo).

## Non-negotiables (why things are the way they are)

- **`gh` must be authed with the `project` scope.** Projects v2 is GraphQL-only;
  a plain push token (`repo` only) returns INSUFFICIENT_SCOPES. The user runs
  `gh auth login -s project` — you cannot do interactive auth.
- **GraphQL Int/Boolean vars need `gh api graphql -F` (typed), not `-f`** (which
  stringifies and the API rejects for `$num: Int!`). The scripts handle this; keep
  it in mind if you extend a query.
- **"Type" is a reserved Projects v2 field name** — the Epic/Story field is
  **"Work Type"**.
- **The scripts never crash their caller.** `syncline_sync.py` wraps everything and
  always `exit(0)` — it is designed to run on every Stop and be invisible unless
  something changed. Preserve that.
- **State is local-only** (lives under `configs/state/`), so board sync does not
  travel to teammates/CI via git. Fine for a personal workflow; flag it if a
  team-wide board sync is wanted.

## Conventions

- Python 3, standard library only (no third-party deps); `gh` is the only external
  binary. Keep it that way — zero-install is a feature.
- Everything is config-driven; do not hard-code a repo/owner/project into the
  scripts. New behavior that varies per project → a config field with a documented
  default.
- When extending: add the capability to the right script, document it in SKILL.md,
  and add any new gotcha to SKILL.md's "Hard-won gotchas" list.
