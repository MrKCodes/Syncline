---
title: 'Story 2.0 — Disposable scratch board + manual-verification checklist'
type: 'chore'
created: '2026-10-22'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
baseline_commit: 'd28b675b0bd194857e16c993b6d8506ee7cdf00a'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 2/3 mutate a real GitHub Projects v2 board destructively, and v1 ships with no CI — so there must be a disposable scratch board to develop against (never a real project first) and a written manual-verification checklist, stated honestly as the verification strategy. Neither exists as documentation yet.

**Approach:** Add one contributor-facing doc, `docs/VERIFYING.md`, that (1) gives reproducible `gh project create` setup for a throwaway Projects v2 board whose `Status` options a sample `status_map` targets, plus the exact teardown command to delete it; and (2) states plainly that v1 has no CI and verification is manual against the scratch board, then provides the dry-run → import → edit-plan → sync → verify checklist (counts, Status, Work Type, sub-issue links, open/closed). Documentation only — no scripts change, no board writes beyond the already-created scratch board.

</frozen-after-approval>

## Implementation Notes

- The maintainer scratch board already exists and is real: `Syncline Scratch Board`, user `MrKCodes` project #4 (`PVT_kwHOAr7-J84BoIso`), https://github.com/users/MrKCodes/projects/4, default Status options Todo / In Progress / Done. Ground the doc's commands in exactly what created it.
- `docs/VERIFYING.md` chosen as the doc home (new contributor/verification doc, distinct from the user-facing `docs/USAGE.md`). Decision mine; user wouldn't notice the filename.
- Scope guard: do NOT run `syncline_import.py` against the board in this story — it still reads the old manifest shape and would error on Epic 1's new-shape manifest; the import migration is Stories 2.2–2.6. This story is setup + checklist docs only.
- Deferred, not fixed here: `docs/USAGE.md` still describes "matched by exact title" and the removed epic rollup ("epic done only if every story is terminal"). Correcting those while the code still has the old behavior would make them more wrong; they belong to the Epic 2 import stories / Story 4.3. Record in deferred-work.md.

## Review Triage Log

Blind Hunter returned 12 findings; all legitimate (AC2 requires a *runnable* checklist). Grouped into 3 root causes, all `patch`, all in Story 2.0 scope — doc rewritten:

- **Not runnable (findings 1,2,3,6,7,12 — patched):** the checklist built a manifest with no input files, never created `configs/state`/`configs/scratch`, used `./`-prefixed paths without stating the relative-to-config-dir rule, and asserted counts/Status/hierarchy/open-closed with no commands. Fixed: added a minimal inline sample plan (`epics.md` + `sprint-status.yaml`), `mkdir -p` step, an explicit path-resolution note, and verified `gh project item-list` / `gh issue list` / sub-issue GraphQL commands for every check. All added commands were run against scratch board #4 to confirm shape.
- **Honesty: target-vs-today (findings 5,8,12b — patched):** on an unmigrated engine a reader would see marker/match-by-key/rich-dry-run/cycle-cap boxes fail and think it a bug. Added a prominent "Target vs. today" banner and `[lands: 2.x]` tags on every box that depends on later Epic-2 work; this directly serves NFR7 honesty.
- **Clarity (findings 4,9,10,11 — patched):** stated case-insensitive option matching, disambiguated board **number** (config/teardown) vs node **id** (`PVT_…`, GraphQL inspect), labeled the create-output JSON abridged, and noted `gh project delete` is irreversible / confirm the number.

No findings rejected; none deferred (all were in-scope doc-quality fixes). Separately, `docs/USAGE.md` pre-migration drift is logged in deferred-work.md (not a review finding — pre-existing, owned by later stories).
