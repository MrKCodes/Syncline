- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-bmad-adapter-emits-a-conforming-v1-manifest.md`
  summary: Manifest write path raises FileNotFoundError if manifest_file's parent dir (e.g. state/) is absent.
  evidence: Pre-existing — `main()` calls `open(manifest_path,"w")` with no `os.makedirs`; identical in the pre-Story-1.1 code, so not caused by this change. Low harm (first write of a fresh config). Settle by adding `os.makedirs(os.path.dirname(manifest_path), exist_ok=True)` before the write.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-bmad-adapter-emits-a-conforming-v1-manifest.md`
  summary: Missing required config keys (status_map, project_name, repo_owner, repo_name, manifest_file) raise a raw KeyError that escapes main()'s `except ManifestError`.
  evidence: Pre-existing — the pre-Story-1.1 code accessed the same keys via `cfg[...]`. The adapter's fail-legibly contract covers manifest content, not config-key presence. Best addressed alongside Story 1.2 (config schema) or Story 4.1 (`--init`), which own config validation/scaffolding.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-bmad-adapter-emits-a-conforming-v1-manifest.md`
  summary: configs/example.config.json's `_done_statuses` comment still says it makes "an epic roll up to done" — a behavior Story 1.1 abolished (no computed rollup).
  evidence: Config is explicitly out of scope for Story 1.1 (spec Boundaries). Story 1.2 (pin and document the config schema) should reconcile the comment and note the `manifest_epic_regex`/`manifest_story_regex` override keys the builder now reads.
