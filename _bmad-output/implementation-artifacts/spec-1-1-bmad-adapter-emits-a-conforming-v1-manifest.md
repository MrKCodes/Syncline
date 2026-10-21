---
title: 'Story 1.1 — BMAD adapter emits a conforming v1 manifest'
type: 'refactor'
created: '2026-10-21'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '6976e639e76e6d9c3d7b6cfe27d02e2cf2840d15'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The BMAD adapter (`bin/syncline_build_manifest.py`) emits the pre-architecture manifest shape — `{project_name, repo, status_map, epics[], stories[]}` — where only stories carry a key and parents do not. AD-5 makes the manifest the one public contract and requires a flat `items[]` list with a durable key on *every* item (parents included); every downstream epic consumes only this shape.

**Approach:** Migrate the adapter in place to emit the AD-5 contract: `{schema_version:1, project, repo, status_map, items[]}` where each item is exactly `{key, parent, type, title, status}`. Epics become items (`type:"epic"`, `parent:null`, key `epic-N`); stories become items (`type:"story"`, `parent:"epic-N"`, key `N-M-slug`). Add fail-legibly pre-write validation for the adapter's own guarantees (status ∈ status_map keys, key grammar, uniqueness). Stays read-only toward GitHub.

## Boundaries & Constraints

**Always:**
- Top-level manifest is exactly `{schema_version, project, repo, status_map, items[]}`; `schema_version` is the integer `1`.
- Each item is exactly `{key, parent, type, title, status}`. Stories: `parent` = their epic's key, `type` = `"story"`. Epics: `parent` = `null`, `type` = `"epic"`.
- Hierarchy is carried only by `parent`; `type` is an opaque label and encodes no structure (AD-6).
- Every `key` is unique within the manifest, matches `^[A-Za-z0-9._-]{1,200}$`, and never contains the substring `-->` (AD-5 marker-safety).
- Every item's `status` is a key of `status_map`; otherwise fail (AC3).
- On any validation failure: name the offending item(s)/value(s), exit non-zero, write no partial manifest.
- Python 3 standard library only; zero GitHub calls; the only output is the manifest file (or stdout under `--print`).
- `*_file` config paths resolve relative to the config file's directory, absolute paths as-is (unchanged behavior).

**Never:**
- Never emit the split `epics[]`/`stories[]` arrays, `project_name`, or `story_count`.
- Never derive hierarchy from `type` or `title`; never use `title` as a key.
- Never perform parent-resolution / acyclicity / GitHub-cap validation — that is the engine's AD-10 job (Story 2.2); do not duplicate it here.
- Never change the config schema or the config files — that is Story 1.2.
- Never add a third-party dependency.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Well-formed `epics.md` + `sprint-status.yaml` | `{schema_version:1, project, repo, status_map, items[]}`; each epic → `{key:"epic-N", parent:null, type:"epic", title, status}`, each story → `{key:"N-M-slug", parent:"epic-N", type:"story", title, status}` | N/A |
| `--print` | Any valid plan | Manifest JSON to stdout; no file written | N/A |
| Status not in map | An item's raw status is absent from `status_map` | Report the offending item key(s) + bad status; write nothing | exit non-zero |
| Duplicate key | Two items resolve to the same key | Report the duplicated key; write nothing | exit non-zero |
| Bad key grammar | A key has out-of-grammar chars or contains `-->` | Report the offending key; write nothing | exit non-zero |

</frozen-after-approval>

## Code Map

- `bin/syncline_build_manifest.py` -- THE file to migrate. Reuse: config load + `rel()` path resolution (lines ~29-57), `## Epic N:` title parse (~70-78), spec-glob story-title parse (~82-96), the `manifest_epic_regex`/`manifest_story_regex` line parse of `sprint-status.yaml` (~66-116). Replace: the manifest assembly (~118-149) and drop the epic rollup + `story_count` logic. Extract a pure `build_manifest(cfg, cfg_dir) -> dict` that raises a clear error on validation failure, so it is unit-testable without subprocess.
- `configs/example.config.json`, `configs/subby.config.json` -- config the adapter reads (`project_name`, `repo_owner`, `repo_name`, `status_map`, `*_file` paths, `manifest_*_regex`). READ-ONLY this story; the adapter keeps reading `cfg["project_name"]` and emits it as the manifest's `project`. Do not edit (Story 1.2).
- `bin/syncline_import.py`, `bin/syncline_sync.py` -- consume the OLD shape; will not read the new manifest until Epic 2 migrates them. Out of scope; do not touch. Expected, per the planned build order.
- `_bmad-output/implementation-artifacts/epic-1-context.md` -- compiled epic context (contract details).

## Tasks & Acceptance

**Execution:**
- [x] `bin/syncline_build_manifest.py` -- Extract `build_manifest(cfg, cfg_dir) -> dict` emitting AD-5 shape: `schema_version:1`, `project` (from `cfg["project_name"]`), `repo`, `status_map`, flat `items[]`. Epic items `{key:"epic-N", parent:null, type:"epic", title:(from epics.md), status:(from its sprint-status line)}`; story items `{key:(N-M-slug), parent:"epic-N", type:"story", title, status}`. Drop `epics[]`/`stories[]`, `story_count`, and the done-rollup. -- the contract migration (FR1, FR2, FR3, FR4, FR8-contract; AD-5, AD-6).
- [x] `bin/syncline_build_manifest.py` -- Add pre-write validation inside `build_manifest`: every `status` is a key of `status_map`; every `key` matches `^[A-Za-z0-9._-]{1,200}$` and excludes `-->`; keys unique. On failure raise with the offending item(s)/value(s); `main()` prints the message and exits non-zero before writing. -- fail-legibly, no partial write (AC3).
- [x] `tests/test_build_manifest.py` -- Unit-test the I/O matrix: happy path (assert top-level keys, `schema_version==1`, epic/story item shapes + parent wiring), `--print`-equivalent (dict return, no file), status-not-in-map raises, duplicate key raises, bad key grammar raises. Load the module from `bin/` via `importlib`; use `tempfile` fixtures. stdlib `unittest` only. -- edge-case coverage.

**Acceptance Criteria:**
- Given a well-formed BMAD plan, when I run the adapter with `--print`, then it prints a manifest `{schema_version, project, repo, status_map, items[]}` with `schema_version` the integer `1`, every item is exactly `{key, parent, type, title, status}`, both epics and stories are in the single flat `items[]`, each story's `parent` is its epic's key, each epic's `parent` is `null`, and no GitHub call is made. _(FR1, FR2, FR3, FR8-contract)_
- Given any item, when the adapter assigns its `key`, then the key is unique, matches `^[A-Za-z0-9._-]{1,200}$`, never contains `-->`; an epic's key is `epic-N`, a story's key is its `N-M-slug`; `type` is the opaque role (`epic`/`story`) and hierarchy is carried only by `parent`. _(FR4, AD-5, AD-6)_
- Given a plan the adapter cannot turn into a valid manifest (status absent from `status_map`, or a duplicate/ill-formed key), when I run it, then it reports the specific offending item(s) and exits non-zero without writing a partial manifest. _(AC3)_

## Implementation Notes

- `bin/syncline_build_manifest.py`: extracted pure `build_manifest(cfg, cfg_dir) -> dict`; `main()` now does config-load → call → print/write. New `ManifestError` carries the legible offender message; `main()` catches it, prints `ERROR: ...`, exits non-zero before any write. `rel()` is used in both `build_manifest` (inputs) and `main()` (manifest output path).
- Items assembled epic-then-its-stories (both sorted) for a readable `--print`; order is not load-bearing (engine topo-sorts per AD-11). Three validations run after assembly: status∈status_map keys, key grammar + `-->`-safety, uniqueness.
- Epic status read directly from its `epic-N:` sprint line; absent → `backlog` (then status-checked). No computed rollup (per Design Note). `done_statuses` is no longer read by the adapter (engine's AD-12 job) but left in configs untouched — Story 1.2's scope.
- Fixed a pre-existing unclosed-file leak in the spec-glob title reader (now `with open(...)`).
- `tests/test_build_manifest.py`: 15 stdlib `unittest` cases, module loaded via `importlib`, `tempfile` fixtures. All 15 pass. Verified real-plan `--print`: 4 epics / 14 stories / 18 unique keys, `schema_version:1`, no file written.
- Out of scope and untouched as required: `configs/*`, `bin/syncline_import.py`, `bin/syncline_sync.py` (Epic 2 migrates the engine to consume the new shape).
- **Review patches applied (2):** (1) orphan-story fix — story emission refactored into a `story_item()` helper; stories whose epic has a `## Epic N:` heading are emitted inline, and a trailing loop emits any story whose epic has no heading (parent still `epic-<N>`), so no story is ever dropped. (2) added `test_epic_status_is_direct_read_not_rollup` (pins rollup removal) and `test_story_without_epic_heading_is_not_dropped`. Suite now 17 tests, all pass. Three review findings deferred (pre-existing missing-dir + missing-cfg-key legibility; config-comment drift → Story 1.2) in `deferred-work.md`.

## Spec Change Log

## Review Triage Log

- **Orphan story silently dropped** (`bin/syncline_build_manifest.py` assembly loop) — `medium`, **patch**. Verified: story emission is nested in `for n in sorted(epic_titles)`, so a story line whose epic has no `## Epic N:` heading is never emitted — no error, no item. The pre-diff code appended every matched story to `stories[]` unconditionally, so this change introduced silent data loss. Contradicts the story's fail-legibly / no-data-loss theme. Fix: emit every story as an item with its `parent` pointer regardless of epic-heading presence (the engine's AD-10 catches a dangling parent later; the adapter must not lose it).
- **Rollup-divergence not pinned by a test** (`tests/test_build_manifest.py`) — `low`, **patch** (verification-gap, pre-verified). Code is correct (direct read of the epic line); no test asserts the one case where direct-read ≠ old rollup (non-done epic line + all-done children). A reintroduced rollup would ship undetected. Fix: add one test asserting such an epic keeps its line status.
- **Missing manifest parent dir → FileNotFoundError** (write path) — `low`, **defer**. Verified pre-existing: pre-diff code had the identical `open(manifest_path,"w")` with no `makedirs`. Not caused by this change.
- **Missing required cfg keys → raw KeyError escapes `except ManifestError`** (`build_manifest`/`main`) — `low`, **defer**. Verified pre-existing: pre-diff code accessed `cfg["project_name"]`/`repo_owner`/`repo_name`/`status_map`/`manifest_file` via `[]` too. Fail-legibly contract in the spec covers manifest content, not config-key presence.
- **Config/doc drift: `done_statuses` comment says "roll up to done"** (`configs/example.config.json`) — `low`, **defer to Story 1.2**. The rollup is abolished, but config is explicitly out of scope here (spec Boundaries: "Never change the config schema or files — that is Story 1.2"). Story 1.2 owns documenting the schema.
- **Validation raises on first category; not exhaustive over fields** — `low`, **rejected**. Error still names the firing category's offenders (AC3 satisfied); `type` is adapter-set and can't be wrong; parent-resolution is the engine's job by spec. Accumulating across categories adds complexity for no acceptance need.
- **KEY_RE 200-char bound + bad-grammar test realism** — `low`, **rejected**. Test-realism critiques, not defects; the grammar check is correct and the suggested extra tests guard inputs unreachable with shipped defaults.
- **Consumers (`import`/`sync`) still read the old shape; pipeline breaks end-to-end** — real but **out of scope (intent-excluded)**. Story 1.1 is adapter-only; the engine migration is Epic 2 (the planned staged build order). Already tracked by Epic 2 in the plan; no new deferred entry needed.

## Design Notes

**Epic status source — decision.** The old code computed an epic rollup (`done` if all children done, else the epic line, else `backlog`). AD-5 treats every item uniformly: `status` is just a key of `status_map` read from the plan. `sprint-status.yaml` already carries an explicit `epic-N:` line per epic, so the epic item's status is read directly from that line — no computed rollup (the adapter injects no state the plan doesn't contain; `done_statuses`-driven closing is the engine's AD-12 job). If an epic heading has no status line, default to `backlog`, then subject it to the same status∈status_map check.

## Verification

**Commands:**
- `python3 tests/test_build_manifest.py` -- expected: all unit tests pass (happy path shape, parent wiring, and the three failure modes).
- `python3 bin/syncline_build_manifest.py --config <tmp-config> --print` against a small temp fixture -- expected: AD-5-shaped JSON on stdout, no `epics[]`/`stories[]`, exit 0.
