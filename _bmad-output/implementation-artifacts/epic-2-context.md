# Epic 2 Context: Manifest → Board (one-time import)

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 2 builds the engine that seeds a GitHub Projects v2 board from a `manifest.json`, turning
a user's plan into real, correctly-shaped work on the board in one idempotent, resumable pass:
issues are created and stamped with an authoritative identity marker, matched by key (never by
title), added to the Project with Status and Work Type set, linked into the epic→story
sub-issue tree, and closed/reopened to reflect terminal status — all behind a whole-plan
`--dry-run` and a fail-legibly-before-any-write validation. This is a brownfield migration: the
existing `syncline_import.py` matches by title and assumes a split `epics[]`/`stories[]` shape;
Epic 2 moves it to the marker-and-key AD contract. The epic matters because it is the first time
Syncline writes to a real board, and it builds the shared engine helpers (marker, validation,
GraphQL, mutations) that Epic 3's sync reuses. Because the engine mutates GitHub destructively,
every story is developed and verified against a disposable scratch board — never a real project
first.

## Stories

- Story 2.0: Disposable scratch board + manual-verification checklist
- Story 2.1: One-time cutover — backfill identity markers, then retire title-matching
- Story 2.2: Pre-write validation + whole-plan dry-run
- Story 2.3: Create + stamp issues, matched by key
- Story 2.4: Place items on the board with Status and Work Type
- Story 2.5: Build the sub-issue hierarchy from parent pointers, in order
- Story 2.6: Reconcile open/closed state from status

## Requirements & Constraints

- Import seeds the board end to end: create real repo issues, add them to the Project (v2), set
  Status + Work Type, link children to parents as native sub-issues, and close/reopen per status.
- On create, stamp a canonically positioned `<!-- syncline-id: KEY -->` marker — on its own line
  at the end of the issue body — into every issue; this marker is the sole authority for identity.
- Match plan items to issues by key via the live marker, never by title. The state cache is only a
  hint and must be verified against the live marker before skipping a create; title is display-only
  and freely editable on either side. Marker-shaped text anywhere but its canonical position
  (including in the title) is ignored.
- Hierarchy (sub-issue links) derives only from `parent` pointers; `type` only selects the Work
  Type option and carries no structural meaning.
- Import is idempotent (match-before-create, no duplicates on re-run) and resumable (state saved
  after each item, so an interrupted run — e.g. a rate-limit 403 — resumes rather than restarts).
- Terminal status (in `done_statuses`) closes the issue; a non-terminal status reopens a closed
  one — enforced in the same reconcile step that sets the Status field so the two never drift.
- `--dry-run` runs the full pre-write validation and prints the entire plan (every item with its
  mapped Status, Work Type, and parent) without any write.
- Pre-write validation (shared by import and sync, one routine, no divergent copy) checks: the
  board's Status options cover every `status_map` value; every `item.status` is a `status_map`
  key; every `key` matches `^[A-Za-z0-9._-]{1,200}$` and is unique; every `parent` resolves, the
  parent graph is acyclic, and no parent exceeds GitHub's caps (100 children, 8 nesting levels).
  On failure it names the offenders and writes nothing.
- Success criterion: a completed import's item counts, Status/Work Type distribution, sub-issue
  links, and open/closed state all match the manifest.
- Honesty constraint: v1 ships with no CI. The verification strategy is explicitly manual against
  the scratch board; the docs must say so plainly and claim correctness only on well-formed inputs
  and a correctly-configured board.

## Technical Decisions

- **Identity marker (AD-3/AD-4):** `<!-- syncline-id: KEY -->`, own line, end of body. `KEY` is
  the manifest item's `key`. Durable identity lives in this marker; the local state file is a
  rebuildable cache keyed by `KEY`. On a cache miss, scan open+closed repo issues and parse markers
  to build a `{KEY: issue}` index. Identity is never derived from the title.
- **Write discipline (AD-7):** only the engine writes GitHub (issues, project items, fields,
  sub-issue links) and the `state_file`/`cache_file`. Adapters are read-only toward GitHub.
- **Ordering (AD-11):** create and stamp every item before linking any sub-issue, or process in
  topological order of `parent`, so `addSubIssue` never references a not-yet-created parent.
  Correct hierarchy must never depend on adapter emit order; a partially-linked resumed run must be
  repairable, never orphaned (re-linking an already-linked child is a no-op, not an error).
- **GitHub access (NFR2):** all via `gh api graphql`. Projects v2 is GraphQL-only (classic REST
  sunset 2025-04-01). `Int!`/`Boolean!` vars use `-F` (typed); strings use `-f`. Single-select
  Status is set by option **ID**, not label — query `options { id name }` first. Does not depend on
  `gh` feature-floor versions.
- **Reserved field name (NFR4):** the Work Type field is literally `Work Type`. A Projects v2 field
  named `Type` cannot be created; if `Work Type` doesn't exist, import creates it as a single-select
  field with exactly that name. `updateProjectV2ItemFieldValue` cannot set Assignees/Labels/
  Milestone (out of scope anyway).
- **Prerequisite (NFR3):** `gh` authed with the `project` scope; a `repo`-only token returns
  `INSUFFICIENT_SCOPES`. Import fails loud with the fix command in this case.
- **Sub-issues:** GitHub API GA (2025-04-09); hard caps 100 sub-issues/parent, 8 nesting levels —
  validated in AD-10. Any preview-header retry is now defensive-only.
- **Config/state (NFR5/NFR6):** nothing (repo/owner/project) hardcoded; one `config.json` per repo
  drives everything; `*_file` paths resolve relative to the config dir (or absolute). State,
  manifest, and cache are engine-owned, local-only under `state/`, gitignored, not shared.
- **Language (NFR1):** Python 3, standard library only, zero third-party packages; `gh` is the only
  external binary.
- **Manifest shape consumed (AD-5):** `{ schema_version:1, project, repo, status_map, items[] }`,
  each item `{ key, parent, type, title, status }`. The flat `items[]` + `parent` pointer replaces
  the old split arrays. This contract is produced in Epic 1; Epic 2 consumes it.
- **Cutover design (Story 2.1, gating):** boards imported by the old title-matching tool carry no
  marker, so a first match-by-key run would duplicate every issue. The cutover is a one-time
  backfill (dry-run first) that stamps markers onto already-imported issues, reusing the OLD
  title-matcher as a bootstrap, and reports anything it cannot confidently match for manual
  resolution. It must classify and act on **three** populations: (a) no state file AND no
  marker-bearing issues → greenfield → no-op; (b) state + issues present but no markers →
  brownfield → run the backfill; (c) state AND markers present → already migrated → no-op. The
  SAME story then DELETES title-matching from the import/sync code ("one matcher, one funeral"),
  leaving exactly one identity mechanism.

## Cross-Story Dependencies

- Story 2.0 (scratch board + checklist) comes first and is the verification substrate for every
  other E2 and E3 story; per-story ACs carry a "verified on scratch board" line.
- Story 2.1 (cutover) GATES the rest of Epic 2 — match-by-key is not trusted until existing boards
  are backfilled and title-matching is removed. Every other E2 story depends on it.
- Story 2.2's single validation routine is reused by import and by Epic 3's sync.
- Stories 2.3 (create/stamp), 2.4 (place with Status/Work Type), 2.5 (link hierarchy), and
  2.6 (open/closed) build the mutation helpers that Epic 3's sync reuses directly.
- Story 2.6 closes the loop by validating a full import against the Story 2.0 checklist.
- Epic 2 consumes the manifest contract (AD-5) and config schema fixed in Epic 1; it does not
  depend on any plan format.
