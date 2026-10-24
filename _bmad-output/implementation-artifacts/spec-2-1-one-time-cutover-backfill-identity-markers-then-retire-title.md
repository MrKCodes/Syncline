---
title: 'Story 2.1 — One-time cutover: backfill identity markers, then retire title-matching'
type: 'feature'
created: '2026-10-22'
status: 'done'
route: 'dispatch'
review_loop_iteration: 1
baseline_commit: '35c59870949f61a91349756f0f55c152c47a32d5'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Boards previously imported by the old tool carry NO `syncline-id` marker, so the new match-by-key engine would duplicate every existing issue. The one durable identity mechanism (AD-3/AD-4 body marker) does not exist in code yet, and the old title-based identity matcher still lives in `syncline_import.py`. Match-by-key cannot be trusted until existing boards are backfilled with markers and the old title matcher is retired — one matcher, one funeral.

**Approach (bounded — relocate, don't rewire):** Add the shared marker primitive (`syncline_markers.py`) that is the sole identity mechanism going forward, and a one-time `syncline_cutover.py` that classifies the board into three populations and, for a dangerous brownfield board, uses the OLD title-matcher as a one-shot bootstrap (dry-run first) to stamp a canonical `<!-- syncline-id: KEY -->` marker onto each already-imported issue, reporting anything it cannot confidently match. In the SAME change, physically remove the title-based IDENTITY matcher from `syncline_import.py` (its write path is guarded pending Story 2.3's marker-based rewrite) and record that `syncline_sync.py` never matched by title. This does NOT rewire import/sync to marker-matching or the new `items[]` manifest shape — stamp-on-create (FR5), match-by-key resolution (FR6), and new-shape consumption stay Story 2.3, per the FR-coverage map.

## Boundaries & Constraints

**Always:**
- The marker is canonically positioned: `<!-- syncline-id: KEY -->` on its own line at the END of the issue body. Only a marker in that canonical position counts as identity; marker-shaped text elsewhere (including the title) is ignored. (AD-3/AD-4)
- Classify exactly three populations and act correctly on each: **(a) greenfield** — no state file AND no marker-bearing issues → **no-op**; **(b) brownfield** — plan items remain to stamp: a state file / already-imported issues exist and at least one plan item the cutover would stamp is NOT yet marked → **run the backfill** (this includes a *partially* stamped board from an interrupted run — it resumes); **(c) already-migrated** — **every** plan item that the cutover would stamp already carries its marker → **no-op**. "Already-migrated" means *fully* migrated, not "any marker present"; a single marker on an otherwise-unstamped board is brownfield, so an interrupted backfill always resumes rather than no-op'ing. The marker remains authoritative for an individual issue's identity (the per-issue resumable skip); only the board-level classification uses completeness. (AD-3: state is a rebuildable cache.)
- Dry-run first: `--dry-run` reports, per plan item, which existing issue it WOULD stamp (via the one-shot title bootstrap) and writes nothing.
- The real backfill stamps the canonical marker into each confidently-matched issue, is idempotent/resumable per-issue (an issue already carrying its marker is skipped, so an interrupted run resumes), and reports every plan item it could NOT confidently match (ambiguous/missing) for manual resolution — it never guesses.
- The one-shot title-matcher bootstrap lives ONLY inside `syncline_cutover.py`. After this story the ONLY title-based matching anywhere in the codebase is (i) that one-shot bootstrap and (ii) `resolve_project` matching the PROJECT by its configured title (board resolution, not item identity — explicitly retained).
- The cutover is the sole writer of GitHub during cutover (AD-7); it stamps marker text by appending to the issue body, preserving the existing body above it.
- Python 3 stdlib only; all GitHub access via `gh api graphql` with `-F` for Int!/Boolean! (NFR1/NFR2). Develop and verify against the Story 2.0 scratch board, never a real project first.

**Never:**
- Never stamp a marker on an issue the bootstrap did not confidently match (no guessing); never stamp a second marker on an already-marked issue.
- Never add marker-based matching, stamp-on-create, or new `items[]`-shape consumption to import/sync here — that is Story 2.3.
- Never remove `resolve_project`'s project-by-title match (there is no other way to resolve the configured project).
- Never leave a second title-based IDENTITY matcher alive outside the one-shot bootstrap.
- Never run the cutover's real (non-dry-run) write path against a real project during development — scratch board only.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Greenfield | no state file AND no issue carries a marker | classify greenfield; no-op; one clear line; exit 0 | N/A |
| Brownfield dry-run | state/issues exist, no markers; `--dry-run` | print per-item: plan key → issue it WOULD stamp (title bootstrap); write nothing | N/A |
| Brownfield real | state/issues exist, no markers; real run | stamp canonical `<!-- syncline-id: KEY -->` on each confidently-matched issue; report unmatched/ambiguous | per-item failure reported, continues |
| Already-migrated | EVERY plan item the cutover would stamp already carries its marker | classify already-migrated; no-op; exit 0 | N/A |
| Partial / resumed | some issues marked, some not (interrupted run) | classify **brownfield** (not all stamped yet); stamp only the still-unmarked matched issues; already-marked skipped | idempotent, no double-stamp |
| Unconfident match | a plan key matches 0 or >1 issue by title | that key is reported for manual resolution; its issue is NOT stamped | reported, not fatal |
| Marker canonical-position | marker-shaped text in title or mid-body, none at canonical end-of-body | treated as NO marker (ignored); position is authoritative | N/A |

## Marker contract (frozen)

- Exact form: a line `<!-- syncline-id: KEY -->` as the LAST non-empty line of the issue body, preceded by a blank line separating it from existing body text.
- `KEY` is the manifest item's `key` (AD-5 grammar, never contains `-->`), so the marker line is unambiguous.
- Extraction accepts only a marker in that canonical trailing position; anything else is not identity.

</frozen-after-approval>

## Open Questions

<!-- none -->

## Code Map

- `bin/syncline_markers.py` -- NEW shared identity primitive (stdlib only, imports no engine code). Provide: `MARKER_RE`, `make_marker(key) -> str` (the canonical line), `append_marker(body, key) -> str` (blank line + marker at end; idempotent if already present), `extract_key(body) -> str|None` (canonical trailing position ONLY), `index_by_marker(issues) -> {key: issue}`. This is the one identity mechanism; cutover and (later) 2.3's import/sync consume it.
- `bin/syncline_cutover.py` -- NEW one-time cutover. `--config` + `--dry-run`. Reuse from `syncline_import.py`: `graphql()`/`gh()` helpers, `load_json`, `rel()`, `resolve_project`, and the issue-pagination scan + the title→issue bootstrap logic (the OLD `existing[title.strip()]` idea, relocated here as the one-shot bootstrap). **Classify by completeness (iteration 1 fix):** `classify(state, issues, items)` returns greenfield when no state AND no markers; **already-migrated only when EVERY plan item in `items` the cutover would stamp already carries its marker**; otherwise brownfield (this includes a partially-stamped board → it resumes). `classify` therefore needs the plan `items` list, not just raw marker presence. Brownfield: `bootstrap_match` maps plan item `key` → issue by title, dry-run prints the mapping, real run appends the marker via a GraphQL `updateIssue(body:)` mutation, reports unconfident. Reads the manifest for plan items (`manifest["items"]` — AD-5 shape from Story 1.1) OR the state file's existing key→issue map; prefer the state file as the authoritative list, falling back to manifest keys. **Fold in review patches:** guard `it.get("key")` in `plan_items` (skip + note, never KeyError); distinguish a *missing* state file (None → a greenfield factor) from an *unreadable/corrupt* one (fail legibly, never silently classify a brownfield board as greenfield); drop dead imports (`sys`/`json`) and the unused `make_marker` bind; fix the `scan_issues` comment that cited now-deleted import.py line numbers.
- `bin/syncline_import.py` -- EDIT: remove the title-based IDENTITY matcher — the `existing` dict built by title (lines ~171-186), `ensure_issue`'s `if title in existing` short-circuit, and `close_issue(existing[s["title"]]...)` (line ~267). Guard the write path with an explicit `SystemExit` naming Story 2.3 (marker-based identity + items[] shape not yet wired) so it fails loud rather than silently duplicating. KEEP `resolve_project` (project-by-title is board resolution, not item identity). Import is already non-runnable on the new manifest since Epic 1; this is the accepted mid-migration state.
- `bin/syncline_sync.py` -- AUDIT (likely no code change): confirm it matches items by STATE-FILE KEY, never by issue title; its only title use (`stitle`/`etitle`, lines ~220-221) names brand-new items (display, not identity) — retain. Add a one-line comment recording the audit result so AC4's "removed from import/sync" is auditable.
- `bin/syncline_import.py` docstring line 13 ("matched by exact title") -- update to reflect retirement.
- `tests/test_cutover.py` + `tests/test_markers.py` -- NEW stdlib unittest. Markers: round-trip make/append/extract, canonical-position-only extraction, idempotent append, index_by_marker. Cutover: the 3-population classifier and the one-shot bootstrap matcher as PURE functions over fixture issue lists (no GitHub) — greenfield/brownfield/already-migrated/partial/unconfident rows of the matrix.

## Tasks & Acceptance

**Execution:**
- [x] `bin/syncline_markers.py` -- NEW: the canonical marker primitive (make/append/extract/index), extraction accepting only the trailing canonical position. -- the single identity mechanism (AD-3/AD-4, FR5-primitive).
- [x] `bin/syncline_cutover.py` -- NEW: `--config`/`--dry-run`; 3-population classify (state × markers, markers authoritative); one-shot title→key bootstrap (relocated old matcher, lives only here); dry-run prints the would-stamp mapping and writes nothing; real run appends canonical markers idempotently and reports unconfident/ambiguous/unmatched. -- the cutover (FR18, AD-3).
- [x] `bin/syncline_import.py` -- EDIT: delete the title-based identity matcher (`existing`-by-title index + its uses); guard the write path with a loud `SystemExit` naming Story 2.3; keep `resolve_project`; fix the "matched by exact title" docstring. -- retire title-matching (AC4, AD-4).
- [x] `bin/syncline_sync.py` -- AUDIT + one-line comment confirming it never matched items by title (state-key only; title is display-naming for new items). -- AC4 completeness for sync.
- [x] `tests/test_markers.py` + `tests/test_cutover.py` -- NEW stdlib unittest covering the marker round-trip + canonical-position rule, and the I/O matrix rows (classify 3 populations + partial + unconfident) as pure functions over fixture issues. -- edge-case coverage.

**Acceptance Criteria:**
- Given a brownfield board (state + issues, no markers), when I run the cutover with `--dry-run`, then it reports per plan item which existing issue it would stamp — matched by the one-shot title bootstrap — and writes nothing. _(FR18)_
- Given I approve and run the cutover for real, when it executes, then it stamps a canonically positioned `<!-- syncline-id: KEY -->` marker (own line, end of body) into each confidently-matched issue, and reports any plan item it could not confidently match for manual resolution. _(FR18, AD-3)_
- Given the three populations, when the cutover inspects state, then it classifies and acts correctly on all three: (a) no state AND no marker-bearing issues → greenfield → no-op; (b) plan items remain to stamp (state + issues, not all markers present — **including a partially-stamped board**) → brownfield → backfill/resume; (c) EVERY plan item already carries its marker → already-migrated → no-op. _(three cases; already-migrated means fully migrated, so an interrupted run resumes — iteration 1 fix)_
- Given the cutover has landed (or was a greenfield no-op), when the story completes, then title-based identity matching is removed from the import/sync code — leaving exactly one identity mechanism (the marker), with the one-shot title bootstrap living only inside the cutover and `resolve_project`'s project-by-title retained as board resolution. _(one matcher, one funeral; AD-4)_
- Given the Story 2.0 scratch board seeded with marker-less issues (simulating an old-tool import), when I run the cutover dry-run then real, then the scratch issues gain correct markers and a re-run classifies already-migrated → no-op. _(verified on scratch board)_

## Implementation Notes

- New `bin/syncline_markers.py` — the single identity primitive (make/append/extract/index); `extract_key` accepts a marker only as the last non-empty body line (AD-4 position-authoritative); `append_marker` idempotent per-key, preserves body.
- New `bin/syncline_cutover.py` — `--config`/`--dry-run`. Pure logic (`plan_items`/`classify`/`bootstrap_match`) split from the GitHub driver (`scan_issues`/`stamp_issue`). Reuses import's `graphql`/`load_json`/`resolve_project` by-path. 3-population classify; one-shot title bootstrap that never guesses; idempotent/resumable backfill.
- `bin/syncline_import.py` — title-identity matcher deleted (the `existing`-by-title index + its uses); real write path replaced with a loud `SystemExit` naming Story 2.3; `resolve_project` kept (project-by-title board resolution); docstring + dead `import sys` cleaned. Import was already non-runnable on the new manifest since Story 1.1; the guard makes that honest.
- `bin/syncline_sync.py` — AC4 audit comment only: sync reconciles by state-file key, never by issue title (its `stitle`/`etitle` name brand-new items — display, not identity). No title-identity matcher existed in sync to retire.
- **Review: 2 iterations.** Iteration 1 found a bad_spec contradiction (the frozen "any marker ⇒ already-migrated" classify rule defeated the frozen "interrupted run resumes" requirement). With user approval the frozen Boundaries + I/O matrix were amended so "already-migrated" means *fully* migrated; code re-derived (classify checks completeness; main()-flow resume test added). Iteration 2 (re-review of the re-derived code) found 4 patches — all introduced/exposed by the iteration-1 fix — now fixed + pinned: (1) already-migrated must also require no unconfident items (else they're silently swallowed); (2) a title-matched issue already bearing a *different* key's marker is reported unconfident, never double-stamped (AD-4); (3) corrupt manifest fails legibly (symmetric with the state file); (4) blank/whitespace-only plan title → unconfident, never false-matches an untitled issue. No further loopback (all patch/defer/reject). Deferred: USAGE/README drift, sync old-shape read + sync unmarked-create (all → Story 2.3 / 4.3), import-guard executing test — recorded in deferred-work.md.
- **Tests:** 23 cutover + 19 markers (+ 17 build_manifest regression) pass. The iteration-1 regression is pinned by `test_partially_stamped_board_is_brownfield_not_already_migrated` + `test_full_flow_partial_board_selects_only_unmarked_items`; all 4 iteration-2 fixes have dedicated tests.
- **AC5 (live scratch-board run) OPEN:** not executed autonomously — it creates real GitHub issues and needs a `configs/scratch.config.json` + throwaway repo (production-safety; no throwaway repo configured). The pure logic is fully unit-tested offline (the spec permits this); the live brownfield run is a documented manual step per `docs/VERIFYING.md` (seed marker-less issues → dry-run → real → re-run no-op).

- **Marker primitive (`bin/syncline_markers.py`).** `MARKER_RE` matches a whole
  line `<!-- syncline-id: KEY -->` (KEY in group `key`, `[^\n]+?` since AD-5 keys
  never contain `-->`). `extract_key` finds the LAST non-empty line and returns
  its key only if that line is the marker — so marker-shaped text in the title,
  mid-body, or sharing a line with other text is ignored (AD-4 position is
  authoritative). `append_marker` normalizes trailing whitespace and inserts one
  blank line before the marker; it is idempotent for the SAME key (returns body
  unchanged) and, if the body already carries a DIFFERENT key, appends rather than
  silently rewriting someone else's marker (the cutover guards against that case
  upstream). `index_by_marker` is first-wins on a duplicate key.
- **Cutover pure core.** `classify`, `plan_items`, and `bootstrap_match` are pure
  functions over the loaded state/manifest/issue lists — no GitHub — so the whole
  I/O matrix is unit-tested offline. `classify` treats markers-present as
  authoritative over the state file; empty state (`{"epics":{},"stories":{}}`) is
  treated the same as a missing state file (both → no-state factor). `plan_items`
  takes the KEY LIST from the state file when it has any entries (epics as
  `epic-N`, stories by `key`), else falls back to the manifest keys; titles always
  come from the manifest (`items[]`) since the state file carries no titles — a key
  the manifest doesn't know keeps a `None` title and is reported as unconfident
  ("no title in manifest to match by"). `bootstrap_match` is the relocated
  `existing[title.strip()]` matcher: exactly-one title match → stamp; 0 or >1 →
  unconfident (never guessed); an issue already carrying ITS OWN key marker is
  skipped (resumable), so an interrupted run resumes.
- **GitHub driver.** `scan_issues` mirrors the import engine's pagination but adds
  `body` (the marker lives there). The real run appends via a GraphQL
  `updateIssue(input:{id:,body:})` mutation (sole writer during cutover, AD-7),
  stamping only confidently-matched, not-yet-stamped issues; per-item failures are
  reported and non-fatal (re-run resumes). Helpers (`graphql`, `load_json`,
  `resolve_project`) and the marker primitive are imported from the sibling modules
  by path, so there is one copy of each.
- **import.py retirement.** The `existing`-by-title index, `ensure_issue`'s
  `if title in existing` short-circuit, and `close_issue(existing[s["title"]])`
  are gone. The real write path is now a single loud `SystemExit` naming Story 2.3
  (marker stamp-on-create + match-by-key over the new `items[]` shape); `--dry-run`
  still resolves the project + Status options + Work Type field for validation.
  `resolve_project` (project-by-title board resolution) is retained. Orphaned
  write-path-only locals (`state`/`save_state`/`repo_id`/`mapped`/`done_statuses`)
  were removed so the file carries no misleading dead code. Import was already
  non-runnable end-to-end since Story 1.1 (adapter emits `items[]`, not
  `epics[]`/`stories[]`); the guard makes that honest rather than silently
  duplicating.
- **sync.py audit.** No code change beyond a comment: `reconcile()` keys off the
  STATE-FILE key (`state["epics"]`/`state["stories"]` → `plan.get(key)` →
  `rec["issue"]`), never the issue title. The only title use (`stitle`/`etitle`)
  names BRAND-NEW items on first creation (display, not identity) and is retained.
- **Scratch-board verification NOT run here.** The offline unit tests
  (`test_markers.py` 19, `test_cutover.py` 16, both green) cover the pure
  classify/match/marker logic per the spec's "unit-tested offline" allowance. The
  live scratch-board acceptance (seed marker-less issues on board #4 → cutover
  `--dry-run` → real → re-run no-op) is a destructive manual step that creates real
  GitHub issues in a throwaway repo and needs a `configs/scratch.config.json`
  (gitignored, not present); it is left for the user to execute per
  `docs/VERIFYING.md`. See the final AC and the Verification section below.

## Spec Change Log

### Iteration 1 — partial-resume contradiction in the frozen classify rule
- **Triggering finding:** three review layers converged on one root cause. The frozen classify rule ("ANY canonical marker on the board ⇒ already-migrated", Boundaries + I/O "Already-migrated" row) contradicts the frozen I/O "Partial / resumed" row ("some issues marked, some not → stamp only the unmarked"). An interrupted real backfill leaves *some* markers, so `classify` returns `already-migrated` and `main()` returns before stamping the rest — the backfill is **not resumable through the real flow**, silently stranding unstamped issues. The passing unit test `test_partial_resume_skips_already_stamped` exercises `bootstrap_match` directly and never reaches `classify`, so it hid the gating.
- **Root cause location:** inside `<frozen-after-approval>` (the classify definition). Requires human resolution — routed as intent_gap loopback, frozen block not edited autonomously.
- **Proposed amendment (pending human approval):** "already-migrated" must mean *fully* migrated — every plan item the cutover would stamp already carries its marker — so a *partially* stamped board routes to brownfield and resumes. The marker is still authoritative for a single issue's identity (a per-issue skip), but board-level classification is "all expected markers present" not "any marker present".
- **KEEP (must survive re-derivation):** the pure-logic/driver split; `bootstrap_match`'s correct per-issue resumable skip (it is right — only its reachability through `main` is broken); markers-authoritative-over-state for *identity*; the loud import write-path guard; the sync AC4 audit; the 3-population framing (only the already-migrated *threshold* changes). Also fold in, during re-derivation: guard `it["key"]` in `plan_items` (report, don't KeyError); distinguish a missing state file from an unreadable/corrupt one (corrupt brownfield state must not silently misclassify as greenfield); drop dead imports (`sys`/`json` in cutover, `sys` in import, unused `make_marker` bind); fix the stale `scan_issues` comment that cites now-deleted import.py line numbers.

## Review Triage Log

### Iteration 1 (3 layers: blind-hunter, edge-case, verification-gap)

**bad_spec / intent_gap → loopback (root cause in frozen block):**
- **Partial-resume contradiction** — `high`. Verified: `classify` (cutover.py:119) short-circuits any marked board to already-migrated before `main` reaches the brownfield stamping loop; interrupted backfill cannot resume and silently no-ops. Root cause is two contradicting frozen clauses → human resolution required. (blind-hunter #5 "main() untested", verification-gap "guard/loop untested", edge-case "double-stamp" are three facets of the same gating.)

**Folded into re-derivation (would be patch; code will be re-derived anyway):**
- `it["key"]` unguarded in `plan_items` (cutover.py:87) — `medium`. Verified: bracket access; a manifest item missing `key` raises KeyError and aborts the whole cutover instead of reporting that item.
- Corrupt state-file JSON → `load_json` returns None → classified greenfield no-op (cutover.py:230) — `medium`. Verified: a brownfield board with an unreadable state file silently skips the backfill. Distinguish missing (None) from unreadable (error).
- Dead imports / unused `make_marker` bind — `low`. Verified unused; net-new code should land clean.
- Stale comment citing deleted import.py lines 171-186 (cutover.py:173) — `low`. Verified those lines were deleted in this diff.

**defer (real; pre-existing or disproportionate — recorded in deferred-work.md):**
- `docs/USAGE.md` + `README.md` stale ("matched by exact title"; describe the now-guarded write path) and `syncline_cutover.py` undocumented at top level — `medium`. Pre-existing doc drift, sharper now; owned by the Epic 2 import stories / Story 4.3 honest-limits pass.
- `syncline_sync.py` still reads old `manifest["epics"]/["stories"]` — `medium`. The known mid-migration state; sync's new-shape consumption is 2.3-adjacent, out of this story's bounded scope.
- Import write-path guard has no executing test — `low/medium`. Filed `defer` by the verification-gap layer itself: guard sits behind GitHub I/O, repo has no gh-stub harness, and the guard is transitional to Story 2.3.

**rejected:**
- Double-stamp via `append_marker` with a different key (edge-case) — `false`. Unreachable: a board carrying any marker classifies already-migrated and no-ops before `bootstrap_match`; verification-gap independently confirmed. (Note: once the classify threshold changes in iteration 2, re-confirm this stays guarded.)
- `extract_key` more permissive than AD-5 grammar (blind-hunter #6) — `low`, rejected. Markers are written by Syncline from already-validated keys; hand-authored illegal keys are out of scope ("correct on well-formed inputs"); fix adds validation complexity for an unreachable-in-practice input.

### Iteration 2 (re-review of the re-derived code; 3 layers)

Core fix held — no layer re-raised partial-resume (the central bug is closed). New findings, all `patch` (caused/exposed by this change) — no bad_spec/intent_gap, so no further loopback; fixed in place:

**patch (fixed this iteration):**
- **already-migrated swallows unconfident items** (cutover `classify`) — `medium`, confirmed. My iteration-1 fix returns already-migrated on `any_marker and not matched`, but `matched` is also empty when every item is unconfident — a marker-bearing board with all-unconfident items no-ops and never reports them (violates AC2 + frozen "report, never guess"). Fix: already-migrated requires `not matched AND not unconfident`; otherwise brownfield so unconfident items are reported. (edge-case medium + blind-hunter facet.)
- **double-marker on a wrong-key issue, now reachable** (cutover `bootstrap_match`/stamp loop) — `medium`. The iteration-1 threshold change made the "issue already carries a DIFFERENT key's marker but title-matches another plan key" path reachable through `main` (previously any marker → already-migrated no-op). Stamping it would append a SECOND marker — violates the frozen "never stamp a second marker on an already-marked issue." Fix: `bootstrap_match` treats an issue already identified by another key as unconfident (report conflict, never double-stamp). Closes a hole my own fix opened.
- **corrupt manifest swallowed silently** (cutover `main`) — `low/medium`. State-file corruption now fails legibly, but a corrupt manifest via `load_json(...,None)` degrades all items to unconfident with no hint. Fix: parse the manifest legibly like the state file (symmetric).
- **whitespace-only title false-match** (cutover `bootstrap_match`) — `low`. A plan item whose title strips to "" matches an untitled issue (also ""), false-stamping identity. Fix: treat a blank/whitespace-only title as unconfident.

**defer (recorded in deferred-work.md):**
- **sync.py `ensure()` still creates UNMARKED issues, un-guarded** (mirror of the import guard) — `medium`. Stamp-on-create for sync is explicitly Story 2.3 per the bounded ruling (frozen "Never add stamp-on-create to import/sync here"); sync also can't create until import is rewired in 2.3, so exposure is latent. Recorded sharply: 2.3 must guard/rewire sync's create path too, not only import.
- sync.py reads old `manifest["epics"]/["stories"]` shape; USAGE.md/README stale; GitHub driver (`scan_issues`/`stamp_issue`/`main` real loop) untested — all carried from iteration 1 (the last is the verification-gap layer's own explicit stop-condition: GitHub integration boundary, no offline injection point, repo convention is offline-only).

**rejected:**
- `done_statuses` "documented-but-unread" (blind-hunter #4) — `false`. `syncline_sync.py` still reads `done_statuses` for open/closed; the config comment is accurate for the engine.
- `cfg["state_file"]` hard-indexed (blind-hunter #5 / edge-case) — `low`. A required, documented config key; a missing one fails naming it. Broad config-key-presence validation is deferred work (Story 1.2/4.1), consistent with iteration 1's treatment.
- `scan_issues` null-repository TypeError (edge-case) — `low`. `graphql()` already raises on GraphQL errors; a null `repository` without errors is a GitHub anomaly, not a well-formed-input path.
- redundant `bootstrap_match` recompute in classify + main (blind-hunter #8) — `low`. Harmless recompute over in-memory lists; no behavioral defect.
- KEY containing `-->` round-trip (edge-case low claim) — AD-5 precondition enforced by the manifest builder; out of scope ("correct on well-formed inputs").

## Design Notes

**Why a guard, not a rewire, in import.py.** Import already cannot run end-to-end — since Story 1.1 it reads the old `manifest["epics"]/["stories"]` shape the adapter no longer emits. So removing its title identity and adding a loud guard (rather than a throwaway marker-swap that 2.3 would rewrite) is the honest minimal realization of the bounded scope: the marker primitive is the one identity mechanism, the old matcher is dead except as the cutover's one-shot bootstrap, and import's marker-based identity + items[] consumption lands coherently in 2.3.

**Scratch-board verification.** Board #4 is greenfield, so the brownfield path is exercised by first seeding it with title-only issues (no markers) via `gh`, simulating an old-tool import — then cutover dry-run → real → re-run (no-op). Pure classify/match/marker logic is also unit-tested offline.

## Verification

**Commands:**
- `python3 tests/test_markers.py` -- expected: all pass (round-trip, canonical-position-only, idempotent append, index).
- `python3 tests/test_cutover.py` -- expected: all pass (3-population classify + partial + unconfident). MUST include a test that a **partially-stamped** board classifies **brownfield** (not already-migrated) and that the resume path stamps only the still-unmarked items — the iteration-1 regression — plus the `it["key"]`-missing and corrupt-state cases.
- `python3 -c "import ast; ast.parse(open('bin/syncline_import.py').read()); ast.parse(open('bin/syncline_cutover.py').read()); ast.parse(open('bin/syncline_markers.py').read())"` -- expected: all parse (no syntax breakage from the import.py edits).
- `grep -nE "existing\[.*title" bin/syncline_import.py` -- expected: no matches (title identity matcher gone).
- Scratch-board manual (per docs/VERIFYING.md): seed marker-less issues, run cutover `--dry-run` then real, confirm markers stamped and a re-run is a no-op.
