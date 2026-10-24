#!/usr/bin/env python3
"""
Unit tests for the one-time cutover's PURE logic (`bin/syncline_cutover.py`).

Exercises the Story 2.1 I/O & edge-case matrix WITHOUT touching GitHub: the
three-population classifier (greenfield / brownfield / already-migrated where
already-migrated means FULLY migrated, so a partially-stamped board is brownfield
and resumes), the one-shot title bootstrap matcher (confident match, unconfident
0/>1, resumable skip of already-stamped issues), and plan_items (state file
authoritative over manifest for the key list, manifest for titles). One CLI test
covers the corrupt-but-exists state file failing legibly before any GitHub call.
stdlib `unittest` only; the module is loaded from `bin/` via importlib.

Run: python3 tests/test_cutover.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MODULE_PATH = os.path.join(REPO, "bin", "syncline_cutover.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("syncline_cutover", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_module()


def issue(number, title, body="imported body"):
    return {"id": f"ISS_{number}", "number": number, "title": title, "body": body, "state": "OPEN"}


def marked(number, title, key):
    return issue(number, title, f"imported body\n\n<!-- syncline-id: {key} -->")


# A small brownfield fixture: a state file (old shape) + a manifest (new items[]
# shape) + the matching issues on the board, none carrying a marker yet.
STATE = {
    "epics": {"1": {"issue": 5, "item": "I5"}},
    "stories": {
        "1-1-alpha": {"issue": 13, "item": "I13", "epic": 1},
        "1-2-beta": {"issue": 14, "item": "I14", "epic": 1},
    },
}
MANIFEST = {
    "items": [
        {"key": "epic-1", "parent": None, "type": "epic", "title": "Epic 1: First", "status": "in-progress"},
        {"key": "1-1-alpha", "parent": "epic-1", "type": "story", "title": "1.1 Alpha", "status": "done"},
        {"key": "1-2-beta", "parent": "epic-1", "type": "story", "title": "1.2 Beta", "status": "in-progress"},
    ],
}
ISSUES = [
    issue(5, "Epic 1: First"),
    issue(13, "1.1 Alpha"),
    issue(14, "1.2 Beta"),
]


# The plan items the cutover would back-fill for the STATE/MANIFEST fixture.
ITEMS = MOD.plan_items(STATE, MANIFEST)

# A FULLY-stamped board: every plan item's issue already carries its own marker.
FULLY_STAMPED = [
    marked(5, "Epic 1: First", "epic-1"),
    marked(13, "1.1 Alpha", "1-1-alpha"),
    marked(14, "1.2 Beta", "1-2-beta"),
]

# A PARTIALLY-stamped board: #13 stamped, the other two not (interrupted run).
PARTIALLY_STAMPED = [
    issue(5, "Epic 1: First"),
    marked(13, "1.1 Alpha", "1-1-alpha"),
    issue(14, "1.2 Beta"),
]


class TestClassify(unittest.TestCase):
    def test_greenfield_no_state_no_markers(self):
        # No state file (None) AND no marker-bearing issues -> greenfield.
        self.assertEqual(MOD.classify(None, [issue(1, "Random")], []), "greenfield")
        self.assertEqual(MOD.classify(None, [], []), "greenfield")

    def test_empty_state_counts_as_no_state(self):
        empty = {"epics": {}, "stories": {}}
        self.assertEqual(MOD.classify(empty, [issue(1, "Random")], []), "greenfield")

    def test_brownfield_state_but_no_markers(self):
        self.assertEqual(MOD.classify(STATE, ISSUES, ITEMS), "brownfield")

    def test_already_migrated_only_when_fully_stamped(self):
        # EVERY plan item already carries its marker -> fully migrated -> no-op.
        self.assertEqual(MOD.classify(STATE, FULLY_STAMPED, ITEMS), "already-migrated")

    def test_partially_stamped_board_is_brownfield_not_already_migrated(self):
        # Some issues carry their marker, some don't (interrupted run): the board
        # is NOT fully migrated, so it must classify brownfield and RESUME — not
        # no-op and strand the still-unstamped issues. (The core bug this fixes.)
        self.assertEqual(MOD.classify(STATE, PARTIALLY_STAMPED, ITEMS), "brownfield")

    def test_full_flow_partial_board_selects_only_unmarked_items(self):
        # Full-flow shape: classify brownfield, THEN bootstrap_match selects only
        # the still-unmarked items (the already-stamped #13 is skipped). This is
        # the path main() now reaches for a partial board.
        self.assertEqual(MOD.classify(STATE, PARTIALLY_STAMPED, ITEMS), "brownfield")
        matched, unconfident = MOD.bootstrap_match(ITEMS, PARTIALLY_STAMPED)
        self.assertEqual(unconfident, [])
        self.assertEqual(set(matched.keys()), {"epic-1", "1-2-beta"})
        self.assertNotIn("1-1-alpha", matched)  # already stamped -> not re-selected

    def test_single_marker_on_otherwise_unstamped_board_is_brownfield(self):
        # A single canonical marker on a board whose other plan items are still
        # unstamped is brownfield (a marker is authoritative for ITS issue's
        # identity, but board-level completeness is what classification uses).
        issues = [marked(1, "Anything", "k"), issue(5, "Epic 1: First")]
        self.assertEqual(MOD.classify(STATE, issues, ITEMS), "brownfield")

    def test_marker_present_but_items_unconfident_is_brownfield_not_migrated(self):
        # A marker-bearing board whose plan items are all UNCONFIDENT (no title
        # match) leaves bootstrap_match with empty `matched` but non-empty
        # `unconfident`. That must classify brownfield so the normal path REPORTS
        # the unconfident items — never already-migrated (which would silently
        # swallow them). Guards the "report unmatched, never guess" rule.
        issues = [marked(99, "Some Unrelated Stamped Issue", "zz-leftover")]
        self.assertEqual(MOD.classify(STATE, issues, ITEMS), "brownfield")
        matched, unconfident = MOD.bootstrap_match(ITEMS, issues)
        self.assertEqual(matched, {})
        self.assertTrue(unconfident)  # the plan items are reported, not swallowed

    def test_marker_shaped_text_mid_body_does_not_trip_already_migrated(self):
        # A marker NOT in the canonical trailing position is not identity, so a
        # board carrying only such text with a state file is still brownfield.
        decoy = issue(5, "Epic 1: First", "see <!-- syncline-id: x --> inline\n\nmore body")
        self.assertEqual(MOD.classify(STATE, [decoy], ITEMS), "brownfield")


class TestPlanItems(unittest.TestCase):
    def test_state_file_is_authoritative_key_list(self):
        items = MOD.plan_items(STATE, MANIFEST)
        keys = [k for k, _ in items]
        self.assertEqual(set(keys), {"epic-1", "1-1-alpha", "1-2-beta"})
        titles = dict(items)
        self.assertEqual(titles["epic-1"], "Epic 1: First")
        self.assertEqual(titles["1-1-alpha"], "1.1 Alpha")

    def test_falls_back_to_manifest_keys_when_no_state(self):
        items = MOD.plan_items(None, MANIFEST)
        self.assertEqual({k for k, _ in items}, {"epic-1", "1-1-alpha", "1-2-beta"})

    def test_key_in_state_but_not_manifest_has_none_title(self):
        state = {"epics": {}, "stories": {"9-9-ghost": {"issue": 99, "item": "I99", "epic": 9}}}
        items = MOD.plan_items(state, MANIFEST)
        self.assertEqual(items, [("9-9-ghost", None)])

    def test_manifest_item_missing_key_is_skipped_not_fatal(self):
        # A malformed manifest item with no `key` must be skipped (via .get),
        # never raise KeyError and abort the whole cutover.
        manifest = {"items": [
            {"key": "epic-1", "title": "Epic 1: First"},
            {"title": "orphan with no key"},            # malformed: no key
            {"key": "1-1-alpha", "title": "1.1 Alpha"},
        ]}
        items = MOD.plan_items(None, manifest)          # None state -> manifest keys
        self.assertEqual({k for k, _ in items}, {"epic-1", "1-1-alpha"})


class TestBootstrapMatch(unittest.TestCase):
    def test_confident_match_all_items(self):
        items = MOD.plan_items(STATE, MANIFEST)
        matched, unconfident = MOD.bootstrap_match(items, ISSUES)
        self.assertEqual(unconfident, [])
        self.assertEqual(set(matched.keys()), {"epic-1", "1-1-alpha", "1-2-beta"})
        self.assertEqual(matched["1-1-alpha"]["number"], 13)
        self.assertEqual(matched["epic-1"]["number"], 5)

    def test_unconfident_zero_matches(self):
        items = [("1-1-alpha", "Title Not On Board")]
        matched, unconfident = MOD.bootstrap_match(items, ISSUES)
        self.assertEqual(matched, {})
        self.assertEqual(len(unconfident), 1)
        self.assertEqual(unconfident[0][0], "1-1-alpha")
        self.assertIn("no issue", unconfident[0][1].lower())

    def test_unconfident_ambiguous_multiple_matches(self):
        # Two issues share the same title -> ambiguous, not stamped.
        issues = [issue(13, "Dup Title"), issue(14, "Dup Title")]
        items = [("1-1-alpha", "Dup Title")]
        matched, unconfident = MOD.bootstrap_match(items, issues)
        self.assertEqual(matched, {})
        self.assertEqual(len(unconfident), 1)
        self.assertIn("2 issues", unconfident[0][1])
        self.assertIn("#13", unconfident[0][1])
        self.assertIn("#14", unconfident[0][1])

    def test_unconfident_missing_title(self):
        # A key whose manifest title is None cannot be matched -> reported.
        items = [("9-9-ghost", None)]
        matched, unconfident = MOD.bootstrap_match(items, ISSUES)
        self.assertEqual(matched, {})
        self.assertEqual(unconfident[0][0], "9-9-ghost")
        self.assertIn("title", unconfident[0][1].lower())

    def test_partial_resume_skips_already_stamped(self):
        # Interrupted run: #13 already carries ITS marker; #5 and #14 do not.
        # Resume stamps only the unmarked matched issues; the stamped one is
        # neither re-stamped nor reported unconfident.
        items = MOD.plan_items(STATE, MANIFEST)
        issues = [
            issue(5, "Epic 1: First"),
            marked(13, "1.1 Alpha", "1-1-alpha"),  # already done
            issue(14, "1.2 Beta"),
        ]
        matched, unconfident = MOD.bootstrap_match(items, issues)
        self.assertEqual(unconfident, [])
        self.assertEqual(set(matched.keys()), {"epic-1", "1-2-beta"})
        self.assertNotIn("1-1-alpha", matched)

    def test_title_matched_but_different_key_marker_is_unconfident(self):
        # An issue whose title matches but which already carries a DIFFERENT key's
        # marker must NOT be matched/stamped (that would append a second marker,
        # violating AD-4 "one marker"). It is reported unconfident for manual
        # resolution, naming the conflicting key.
        items = [("1-1-alpha", "1.1 Alpha")]
        issues = [marked(13, "1.1 Alpha", "some-other-key")]
        matched, unconfident = MOD.bootstrap_match(items, issues)
        self.assertEqual(matched, {})
        self.assertEqual(unconfident[0][0], "1-1-alpha")
        self.assertIn("some-other-key", unconfident[0][1])

    def test_whitespace_tolerant_title_match(self):
        items = [("1-1-alpha", "  1.1 Alpha  ")]
        issues = [issue(13, "1.1 Alpha")]
        matched, _ = MOD.bootstrap_match(items, issues)
        self.assertEqual(matched["1-1-alpha"]["number"], 13)

    def test_blank_plan_title_is_unconfident_not_matched_to_untitled_issue(self):
        # A plan item whose title strips to "" must NOT match an untitled issue
        # (whose title also strips to "") — that would false-stamp identity. Such
        # a title is reported unconfident, exactly like a missing title.
        for blank in ("", "   ", "\t\n "):
            items = [("1-1-alpha", blank)]
            issues = [issue(13, ""), issue(14, "   ")]  # untitled issues on the board
            matched, unconfident = MOD.bootstrap_match(items, issues)
            self.assertEqual(matched, {}, f"blank title {blank!r} must not match")
            self.assertEqual(len(unconfident), 1)
            self.assertEqual(unconfident[0][0], "1-1-alpha")
            self.assertIn("title", unconfident[0][1].lower())


class TestCorruptStateFailsLegibly(unittest.TestCase):
    """A state file that EXISTS but cannot be parsed must fail legibly, not be
    silently treated as a missing (greenfield) state file. The check runs in
    main() BEFORE any GitHub call, so this exercises it via the CLI with no
    network."""

    def test_corrupt_but_existing_state_file_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "import-state.json")
            with open(state_path, "w") as fh:
                fh.write("{ this is not valid json ]")   # corrupt, but exists
            cfg = {
                "project_name": "Fixture",
                "repo_owner": "OwnerOrg", "owner_type": "org", "repo_name": "repo",
                "state_file": "import-state.json",
            }
            cfg_path = os.path.join(tmp, "cfg.json")
            with open(cfg_path, "w") as fh:
                json.dump(cfg, fh)
            proc = subprocess.run(
                [sys.executable, MODULE_PATH, "--config", cfg_path, "--dry-run"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            msg = proc.stdout + proc.stderr
            self.assertIn("could not be parsed", msg)
            self.assertIn("import-state.json", msg)

    def test_corrupt_but_existing_manifest_file_exits_nonzero(self):
        # Symmetric with the state file: a manifest path that EXISTS but fails to
        # parse must fail legibly (not silently become None, which would make
        # every plan item unconfident for a hidden reason). Absent manifest stays
        # optional. The check precedes any GitHub call, so no network is needed.
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = os.path.join(tmp, "manifest.json")
            with open(manifest_path, "w") as fh:
                fh.write("{ not valid json :::")   # corrupt, but exists
            cfg = {
                "project_name": "Fixture",
                "repo_owner": "OwnerOrg", "owner_type": "org", "repo_name": "repo",
                "state_file": "import-state.json",   # absent -> fine
                "manifest_file": "manifest.json",
            }
            cfg_path = os.path.join(tmp, "cfg.json")
            with open(cfg_path, "w") as fh:
                json.dump(cfg, fh)
            proc = subprocess.run(
                [sys.executable, MODULE_PATH, "--config", cfg_path, "--dry-run"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            msg = proc.stdout + proc.stderr
            self.assertIn("could not be parsed", msg)
            self.assertIn("manifest.json", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
