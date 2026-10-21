#!/usr/bin/env python3
"""
Unit tests for the BMAD manifest adapter (`bin/syncline_build_manifest.py`).

Exercises the Story 1.1 I/O & edge-case matrix against the AD-5 contract:
happy path shape + parent wiring, the dict-return / no-file-written guarantee,
and the three fail-legibly modes (status not in status_map, duplicate key,
bad key grammar). stdlib `unittest` only; the module under test is loaded from
`bin/` via importlib and every plan/config is a `tempfile` fixture.

Run: python3 tests/test_build_manifest.py
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
MODULE_PATH = os.path.join(REPO, "bin", "syncline_build_manifest.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("syncline_build_manifest", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_module()


# A reasonable status_map for the fixtures, mirroring configs/example.config.json.
STATUS_MAP = {
    "done": "Done",
    "review": "In review",
    "in-progress": "In progress",
    "ready-for-dev": "Ready",
    "backlog": "Backlog",
}


EPICS_MD = """\
# Fixture — Epic Breakdown

## Epic 1: Plan to Manifest
Some prose.

## Epic 2: Manifest to Board
More prose.
"""

SPRINT_YAML = """\
development_status:
  epic-1: in-progress
  1-1-bmad-adapter: in-progress
  1-2-config-schema: backlog

  epic-2: backlog
  2-1-cutover: backlog
  2-3-user-auth: review
"""


def _write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


class FixturePlan:
    """Builds a temp dir with epics.md + sprint-status.yaml + a config pointing at
    them, overridable per test. Returns (cfg_dict, cfg_dir)."""

    def __init__(self, tmp, epics=EPICS_MD, sprint=SPRINT_YAML, status_map=None,
                 extra_cfg=None):
        self.dir = tmp
        _write(os.path.join(tmp, "epics.md"), epics)
        _write(os.path.join(tmp, "sprint-status.yaml"), sprint)
        self.cfg = {
            "project_name": "Fixture",
            "repo_owner": "OwnerOrg",
            "repo_name": "repo",
            "status_map": status_map if status_map is not None else dict(STATUS_MAP),
            "epics_file": "epics.md",
            "plan_file": "sprint-status.yaml",
            "manifest_file": "state/manifest.json",
        }
        if extra_cfg:
            self.cfg.update(extra_cfg)


class TestHappyPath(unittest.TestCase):
    def _build(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            fx = FixturePlan(tmp, **kw)
            return MOD.build_manifest(fx.cfg, fx.dir)

    def test_top_level_shape(self):
        m = self._build()
        self.assertEqual(
            set(m.keys()),
            {"schema_version", "project", "repo", "status_map", "items"},
        )
        self.assertEqual(m["schema_version"], 1)
        self.assertIsInstance(m["schema_version"], int)
        self.assertEqual(m["project"], "Fixture")
        self.assertEqual(m["repo"], "OwnerOrg/repo")
        self.assertEqual(m["status_map"], STATUS_MAP)

    def test_no_legacy_keys(self):
        m = self._build()
        for forbidden in ("epics", "stories", "project_name", "story_count"):
            self.assertNotIn(forbidden, m)

    def test_every_item_exact_shape(self):
        m = self._build()
        for it in m["items"]:
            self.assertEqual(
                set(it.keys()), {"key", "parent", "type", "title", "status"},
                f"item {it.get('key')!r} has wrong field set",
            )

    def test_epics_and_stories_in_one_flat_list(self):
        m = self._build()
        types = {it["type"] for it in m["items"]}
        self.assertEqual(types, {"epic", "story"})
        keys = [it["key"] for it in m["items"]]
        self.assertIn("epic-1", keys)
        self.assertIn("epic-2", keys)
        self.assertIn("1-1-bmad-adapter", keys)

    def test_parent_wiring(self):
        m = self._build()
        by_key = {it["key"]: it for it in m["items"]}
        # Epics are roots.
        self.assertIsNone(by_key["epic-1"]["parent"])
        self.assertIsNone(by_key["epic-2"]["parent"])
        self.assertEqual(by_key["epic-1"]["type"], "epic")
        # Each story's parent is its epic's key.
        self.assertEqual(by_key["1-1-bmad-adapter"]["parent"], "epic-1")
        self.assertEqual(by_key["1-2-config-schema"]["parent"], "epic-1")
        self.assertEqual(by_key["2-1-cutover"]["parent"], "epic-2")
        self.assertEqual(by_key["2-3-user-auth"]["parent"], "epic-2")
        self.assertEqual(by_key["2-3-user-auth"]["type"], "story")

    def test_epic_key_tokens_and_story_slugs(self):
        m = self._build()
        for it in m["items"]:
            if it["type"] == "epic":
                self.assertRegex(it["key"], r"^epic-\d+$")
            else:
                self.assertRegex(it["key"], r"^\d+-\d+-[a-z0-9-]+$")

    def test_status_read_from_plan_line(self):
        # AD-5 design note: epic status is read directly from its sprint line,
        # not a computed rollup. epic-1 line says in-progress even though its
        # children are not all done.
        m = self._build()
        by_key = {it["key"]: it for it in m["items"]}
        self.assertEqual(by_key["epic-1"]["status"], "in-progress")
        self.assertEqual(by_key["epic-2"]["status"], "backlog")
        self.assertEqual(by_key["2-3-user-auth"]["status"], "review")

    def test_epic_without_status_line_defaults_backlog(self):
        # Epic 3 heading present in epics.md but NO epic-3 line in the plan.
        epics = EPICS_MD + "\n## Epic 3: Orphan Epic\n"
        m = self._build(epics=epics)
        by_key = {it["key"]: it for it in m["items"]}
        self.assertEqual(by_key["epic-3"]["status"], "backlog")

    def test_epic_status_is_direct_read_not_rollup(self):
        # Pins the rollup removal (AD-5 design note): the epic line says
        # in-progress while ALL its children are done. The old code rolled an
        # epic up to "done" in that case; the direct-read must keep "in-progress".
        epics = "## Epic 1: All Children Done\n"
        sprint = (
            "development_status:\n"
            "  epic-1: in-progress\n"
            "  1-1-first: done\n"
            "  1-2-second: done\n"
        )
        m = self._build(epics=epics, sprint=sprint)
        by_key = {it["key"]: it for it in m["items"]}
        self.assertEqual(by_key["epic-1"]["status"], "in-progress")

    def test_story_without_epic_heading_is_not_dropped(self):
        # A story line whose epic has no `## Epic N:` heading must still become an
        # item (parent set to epic-<N>); the adapter must not silently lose it.
        # epics.md has only Epic 1; the plan carries epic-9 stories.
        epics = "## Epic 1: Only Epic\n"
        sprint = (
            "development_status:\n"
            "  epic-1: in-progress\n"
            "  1-1-known: backlog\n"
            "  9-1-orphan: review\n"
            "  9-2-orphan-two: backlog\n"
        )
        m = self._build(epics=epics, sprint=sprint)
        by_key = {it["key"]: it for it in m["items"]}
        self.assertIn("9-1-orphan", by_key)
        self.assertIn("9-2-orphan-two", by_key)
        self.assertEqual(by_key["9-1-orphan"]["parent"], "epic-9")
        self.assertEqual(by_key["9-1-orphan"]["type"], "story")
        self.assertEqual(by_key["9-1-orphan"]["status"], "review")
        # No epic-9 heading, so no epic-9 item is synthesized.
        self.assertNotIn("epic-9", by_key)

    def test_spec_glob_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                os.path.join(tmp, "spec-1-1-bmad-adapter.md"),
                "---\ntitle: 'Story 1.1 — Shiny Title'\n---\nbody\n",
            )
            fx = FixturePlan(
                tmp, extra_cfg={"story_spec_glob": "spec-*.md"}
            )
            m = MOD.build_manifest(fx.cfg, fx.dir)
            by_key = {it["key"]: it for it in m["items"]}
            self.assertEqual(by_key["1-1-bmad-adapter"]["title"], "1.1 Shiny Title")


class TestReturnsDictNoFile(unittest.TestCase):
    def test_build_returns_dict_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = FixturePlan(tmp)
            m = MOD.build_manifest(fx.cfg, fx.dir)
            self.assertIsInstance(m, dict)
            # build_manifest never writes; the configured manifest path stays absent.
            self.assertFalse(os.path.exists(os.path.join(tmp, "state", "manifest.json")))

    def test_print_cli_writes_no_file(self):
        # The --print path prints to stdout and writes no manifest file.
        with tempfile.TemporaryDirectory() as tmp:
            fx = FixturePlan(tmp)
            cfg_path = os.path.join(tmp, "cfg.json")
            _write(cfg_path, json.dumps(fx.cfg))
            proc = subprocess.run(
                [sys.executable, MODULE_PATH, "--config", cfg_path, "--print"],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)
            self.assertEqual(out["schema_version"], 1)
            self.assertNotIn("epics", out)
            self.assertNotIn("stories", out)
            self.assertFalse(os.path.exists(os.path.join(tmp, "state", "manifest.json")))


class TestFailLegibly(unittest.TestCase):
    def test_status_not_in_map_raises(self):
        # 2-3-user-auth has status "review"; drop it from the map.
        bad_map = {k: v for k, v in STATUS_MAP.items() if k != "review"}
        with tempfile.TemporaryDirectory() as tmp:
            fx = FixturePlan(tmp, status_map=bad_map)
            with self.assertRaises(MOD.ManifestError) as ctx:
                MOD.build_manifest(fx.cfg, fx.dir)
            msg = str(ctx.exception)
            self.assertIn("2-3-user-auth", msg)   # names the offending item
            self.assertIn("review", msg)           # names the bad status

    def test_duplicate_key_raises(self):
        # Two story lines resolving to the same key.
        sprint = (
            "development_status:\n"
            "  epic-1: in-progress\n"
            "  1-1-dup: backlog\n"
            "  1-1-dup: review\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fx = FixturePlan(tmp, sprint=sprint)
            with self.assertRaises(MOD.ManifestError) as ctx:
                MOD.build_manifest(fx.cfg, fx.dir)
            self.assertIn("1-1-dup", str(ctx.exception))
            self.assertIn("duplicate", str(ctx.exception).lower())

    def test_bad_key_grammar_raises(self):
        # A story key comes straight from the plan's captured slug (group 1), so a
        # key carrying the forbidden "-->" substring must trip the grammar check.
        # The default story slug class is [a-z0-9-] which excludes '>', so widen
        # it via a custom regex to let the marker-unsafe key through to validation.
        # Groups: (1)=key (2)=epicNum (3)=storyNum (4)=status.
        epics = "## Epic 1: Grammar Epic\n"
        extra = {
            "manifest_story_regex": r"^\s{2}((\d+)-(\d+)-[a-z0-9>-]+):\s*(\S+)",
        }
        sprint = (
            "development_status:\n"
            "  epic-1: in-progress\n"
            "  1-1-bad-->key: backlog\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fx = FixturePlan(tmp, sprint=sprint, epics=epics, extra_cfg=extra)
            with self.assertRaises(MOD.ManifestError) as ctx:
                MOD.build_manifest(fx.cfg, fx.dir)
            self.assertIn("grammar", str(ctx.exception).lower())
            self.assertIn("1-1-bad-->key", str(ctx.exception))

    def test_main_exits_nonzero_and_writes_nothing_on_failure(self):
        bad_map = {k: v for k, v in STATUS_MAP.items() if k != "review"}
        with tempfile.TemporaryDirectory() as tmp:
            fx = FixturePlan(tmp, status_map=bad_map)
            cfg_path = os.path.join(tmp, "cfg.json")
            _write(cfg_path, json.dumps(fx.cfg))
            proc = subprocess.run(
                [sys.executable, MODULE_PATH, "--config", cfg_path, "--print"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("2-3-user-auth", proc.stderr)
            # No partial manifest written.
            self.assertFalse(os.path.exists(os.path.join(tmp, "state", "manifest.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
