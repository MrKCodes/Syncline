#!/usr/bin/env python3
"""
Unit tests for the identity marker primitive (`bin/syncline_markers.py`).

Pins the frozen marker contract (AD-3/AD-4): the canonical `<!-- syncline-id:
KEY -->` line as the LAST non-empty line of the body, extraction accepting ONLY
that trailing canonical position, an idempotent append, and index_by_marker.
stdlib `unittest` only; the module under test is loaded from `bin/` via importlib.

Run: python3 tests/test_markers.py
"""
import importlib.util
import os
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MODULE_PATH = os.path.join(REPO, "bin", "syncline_markers.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("syncline_markers", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_module()


class TestMakeMarker(unittest.TestCase):
    def test_canonical_form(self):
        self.assertEqual(MOD.make_marker("2-1-cutover"), "<!-- syncline-id: 2-1-cutover -->")

    def test_epic_key(self):
        self.assertEqual(MOD.make_marker("epic-2"), "<!-- syncline-id: epic-2 -->")


class TestExtractKey(unittest.TestCase):
    def test_extract_from_trailing_marker(self):
        body = "Some body text.\n\n<!-- syncline-id: 2-1-cutover -->"
        self.assertEqual(MOD.extract_key(body), "2-1-cutover")

    def test_extract_tolerates_trailing_whitespace_and_blank_lines(self):
        body = "Body.\n\n<!-- syncline-id: epic-3 -->\n\n   \n"
        self.assertEqual(MOD.extract_key(body), "epic-3")

    def test_body_that_is_only_a_marker(self):
        self.assertEqual(MOD.extract_key("<!-- syncline-id: k -->"), "k")

    def test_none_when_no_marker(self):
        self.assertIsNone(MOD.extract_key("Just some body text, no marker."))

    def test_none_for_empty_or_whitespace_body(self):
        self.assertIsNone(MOD.extract_key(""))
        self.assertIsNone(MOD.extract_key(None))
        self.assertIsNone(MOD.extract_key("   \n\n  "))

    # --- Canonical position is authoritative (AD-4): a marker anywhere but the
    # trailing non-empty line is NOT identity. ---

    def test_marker_in_title_position_mid_body_is_ignored(self):
        body = "<!-- syncline-id: top -->\n\nReal body with no trailing marker."
        self.assertIsNone(MOD.extract_key(body))

    def test_marker_followed_by_more_body_is_ignored(self):
        body = "Body.\n\n<!-- syncline-id: middle -->\n\nMore body text after it."
        self.assertIsNone(MOD.extract_key(body))

    def test_marker_sharing_a_line_with_text_is_ignored(self):
        body = "Body.\n\nsee <!-- syncline-id: inline --> for details"
        self.assertIsNone(MOD.extract_key(body))

    def test_only_the_last_marker_counts_when_two_present(self):
        # A mid-body marker-shaped line plus a canonical trailing one: the
        # trailing one is identity; the mid-body one is ignored.
        body = "<!-- syncline-id: decoy -->\n\nbody\n\n<!-- syncline-id: real -->"
        self.assertEqual(MOD.extract_key(body), "real")


class TestAppendMarker(unittest.TestCase):
    def test_append_to_plain_body(self):
        out = MOD.append_marker("Hello body.", "2-1-cutover")
        self.assertEqual(out, "Hello body.\n\n<!-- syncline-id: 2-1-cutover -->")
        self.assertEqual(MOD.extract_key(out), "2-1-cutover")

    def test_append_to_empty_body_is_just_the_marker(self):
        self.assertEqual(MOD.append_marker("", "k"), "<!-- syncline-id: k -->")
        self.assertEqual(MOD.append_marker("   \n ", "k"), "<!-- syncline-id: k -->")

    def test_append_separates_with_exactly_one_blank_line(self):
        # Trailing newlines on the incoming body are normalized to one blank line.
        out = MOD.append_marker("Body.\n\n\n", "k")
        self.assertEqual(out, "Body.\n\n<!-- syncline-id: k -->")

    def test_idempotent_same_key(self):
        once = MOD.append_marker("Body.", "k")
        twice = MOD.append_marker(once, "k")
        self.assertEqual(once, twice)              # no second marker appended
        self.assertEqual(twice.count("syncline-id"), 1)

    def test_round_trip_make_append_extract(self):
        for key in ("2-1-cutover", "epic-2", "a.b_c-1"):
            out = MOD.append_marker("Original body.", key)
            self.assertIn(MOD.make_marker(key), out)
            self.assertEqual(MOD.extract_key(out), key)
            self.assertTrue(out.startswith("Original body."))  # body preserved above


class TestIndexByMarker(unittest.TestCase):
    def test_indexes_only_marker_bearing_issues(self):
        issues = [
            {"number": 1, "body": "b1\n\n<!-- syncline-id: epic-1 -->"},
            {"number": 2, "body": "no marker here"},
            {"number": 3, "body": "b3\n\n<!-- syncline-id: 1-1-alpha -->"},
        ]
        idx = MOD.index_by_marker(issues)
        self.assertEqual(set(idx.keys()), {"epic-1", "1-1-alpha"})
        self.assertEqual(idx["epic-1"]["number"], 1)
        self.assertEqual(idx["1-1-alpha"]["number"], 3)

    def test_empty_when_no_markers(self):
        issues = [{"number": 1, "body": "x"}, {"number": 2, "body": None}]
        self.assertEqual(MOD.index_by_marker(issues), {})

    def test_first_issue_wins_on_duplicate_key(self):
        issues = [
            {"number": 10, "body": "a\n\n<!-- syncline-id: dup -->"},
            {"number": 11, "body": "b\n\n<!-- syncline-id: dup -->"},
        ]
        idx = MOD.index_by_marker(issues)
        self.assertEqual(idx["dup"]["number"], 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
