---
title: 'Story 1.2 — Pin and document the config schema'
type: 'chore'
created: '2026-10-22'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
baseline_commit: '4c2bbd3b90faee84066a9363222be333debd0b62'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `configs/example.config.json` is the documented template a stranger fills in, and the one Epic 4's `--init` will scaffold, but it does not yet document *every* field the adapter and engine read: the two manifest regexes (`manifest_epic_regex`/`manifest_story_regex`) the adapter reads are undocumented, `label` is uncommented, and the `_done_statuses` comment still claims it makes "an epic roll up to done" — a behavior Story 1.1 abolished (no computed rollup). The schema must be the single authoritative shape, not fields discovered ad hoc later.

**Approach:** Make `configs/example.config.json` document the complete, authoritative field set every script reads — each field carrying an inline comment and a sensible default where one exists — using the existing `_<field>` sibling-key comment idiom (standard JSON has no comments). Confirm in the comments that `*_file` paths resolve relative to the config file's own directory (absolute used as-is) and that nothing (repo/owner/project) is hardcoded in the scripts. Documentation only — no script behavior changes, no GitHub, the author's live `configs/subby.config.json` left untouched.

</frozen-after-approval>

## Implementation Notes

- Rewrote `configs/example.config.json` so every real field is documented via a preceding `_<field>` comment key. Verified programmatically: the 20 documented fields exactly equal the union of keys read across all three scripts (`read-but-undocumented: NONE`, `documented-but-unread: NONE`, no orphan `_` comment keys).
- Added the two previously-undocumented adapter fields `manifest_epic_regex`/`manifest_story_regex` (and documented their distinct capture-group contract vs the sync's `epic_line_regex`/`story_line_regex`) in the `_regexes` block; documented `label` (default = project_name); clarified `status_map` (missing status fails validation), `done_statuses` (closes/reopens — dropped the stale "roll up to done" wording per the Story 1.1 deferred finding), and the relative-vs-absolute `*_file` resolution in `_paths_note`.
- AC2 confirmed: `os.path.isabs`-based `rel()` present in all three scripts. AC3 confirmed: grep found no hardcoded owner/repo/project literals (only the word "Syncline" in argparse descriptions/comments).
- Scope held: documentation-only, no script behavior changed, no GitHub. The author's live `configs/subby.config.json` left untouched (it is a filled-in instance, not the template).

## Review Triage Log

Blind Hunter returned 12 findings. 4 patched, 8 rejected/out-of-scope.

**Patched (accuracy of the authoritative doc — the story's point):**
- `type_field_name` "importer creates if absent" was half-true → qualified: import creates except under `--dry-run`; sync never creates. (verified in import.py:141, sync.py has no create path)
- `status_map` "fails validation" overstated → qualified: adapter raises `ManifestError`, sync silently skips an unmapped status. (verified sync.py `parse_plan`/`set_status`)
- `manifest_file` required-vs-optional unstated → added: required by importer (`cfg["manifest_file"]`, exits if unreadable), optional for sync (`cfg.get`). (verified import.py:100, sync.py:105)
- `owner_type` silent footgun → noted: only exact `"org"` selects the org root; other values fall through to user. (verified import.py `resolve_project`)

**Rejected / out-of-scope:**
- Manifest shape + `schema_version` undocumented (2 findings) — the manifest *contract* is Story 1.1 / architecture, not a config field; import/sync still reading the old shape is the known Epic 2 migration state. Out of scope.
- `Status` field must pre-exist; `gh`/`project`-scope prereq (2 findings) — real onboarding prereqs but owned by Story 2.0 + Story 4.3 (honest-limits docs); `status_map` comment already says the board must already have the options. Out of scope, already in plan.
- done_statuses "rollup" — `false`: the doc is accurate for the current close/reopen-only behavior; dropping the stale "roll up to done" wording was the intended fix (Story 1.1 deferred finding).
- label message quoting, regex new-item-path scoping, example path split (3 findings) — `low`, doc not misleading; rejected (fix exceeds a trivial correction for negligible gain).
