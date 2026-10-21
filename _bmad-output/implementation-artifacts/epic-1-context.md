# Epic 1 Context: Plan → Manifest (the contract + BMAD adapter)

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This epic produces the one stable, public contract the entire system is built on: a `manifest.json` that an adapter writes from a plan source, and that the engine (future epics) consumes. The work is to turn a BMAD plan into a valid, inspectable manifest conforming to the v1 contract, and to pin the authoritative config schema the rest of the tool depends on. Everything is verifiable offline via the adapter's `--print` — no GitHub access. Getting this right decouples the engine from any one plan format: later epics depend only on the manifest shape, never on BMAD specifics, and the config schema fixed here is exactly what Epic 4's `--init` will scaffold.

## Stories

- Story 1.1: BMAD adapter emits a conforming v1 manifest
- Story 1.2: Pin and document the config schema

## Requirements & Constraints

- The adapter reads a plan source plus config and writes a conforming manifest; it is strictly read-only toward GitHub (zero GitHub calls) and writes only the manifest file.
- The bundled BMAD adapter converts a BMAD plan — `epics.md` (titles), `sprint-status.yaml` (statuses), and an optional spec-title glob — into a conforming manifest.
- Both epics and stories live in one flat `items[]` array; the old split `epics[]`/`stories[]` shape is removed. Every item carries a durable key, including parents (the pre-architecture gap was that only stories had keys).
- If a plan cannot be turned into a valid manifest (e.g. a status not present in `status_map`, or a duplicate key), the adapter reports the specific offending item(s) and exits non-zero without writing a partial manifest.
- The config schema must document every field the adapter and engine read and resolve `*_file` paths relative to the config file's own directory (absolute paths used as-is). Nothing (repo, owner, project) may be hardcoded — all config-driven.
- Python 3, standard library only; zero third-party packages (NFR1). This governs how the adapter parses `epics.md`/`sprint-status.yaml` — no YAML/JSON libraries beyond stdlib.
- Honesty invariant: no capability may be implied beyond "correct on well-formed inputs."

## Technical Decisions

**Manifest contract shape (v1) — AD-5.** Top-level fields are exactly `{ schema_version, project, repo, status_map, items[] }` with `schema_version` pinned to the integer `1`. Each item has exactly `{ key, parent, type, title, status }`:
- `key` — durable, required, unique within the manifest; must match `^[A-Za-z0-9._-]{1,200}$` and must never contain the substring `-->` (so it is safe inside the identity marker a later epic stamps). This is the item's permanent identity.
- `parent` — the `key` of this item's parent, or `null` for a root. Each story's `parent` is its epic's key; each epic's `parent` is `null`.
- `type` — an opaque label (AD-6); for the BMAD adapter it is `epic`/`story`. It drives only a Work Type field selection downstream and carries no structural meaning.
- `title` — display only, freely editable, never a match key.
- `status` — a raw status that must be a key of `status_map`.

**Key conventions.** Epic keys are stable tokens (e.g. `epic-2`); story keys are stable slugs (e.g. `2-3-user-auth`). Keys must be deterministic and stable across runs so identity survives.

**Hierarchy from `parent` only — AD-6.** The parent/child relationship is carried solely by `parent` pointers. `epic`/`story` is just the default vocabulary the BMAD adapter emits; the contract must not force BMAD nouns onto other plan formats. The adapter must not encode hierarchy via `type` or title.

**Adapter is a program, not an API — AD-1, AD-2.** An adapter is any process that, given a plan source and config, produces a conforming `manifest.json`. It must not import engine code and the engine must never invoke or import it; the manifest file on disk is the only coupling. No plan-format assumptions (BMAD's file names, spec globs) may leak past the manifest.

**Config schema (Story 1.2).** `configs/example.config.json` is the documented template. It must declare every field the adapter and engine read: repo owner/name, project identifier, `status_map`, `done_statuses`, the `Work Type` field name, and the `*_file` paths (plan, manifest, state, cache). Each field gets an inline comment and a sensible default where one exists. State/manifest/cache paths live under a local-only `state/` directory. This is the authoritative schema; Epic 4 scaffolds it rather than inventing fields late.

**Brownfield note.** `bin/syncline_build_manifest.py` currently emits the old `project_name`/`epics[]`/`stories[]` shape and the engine matches by title. Conforming to AD-5 (flat `items[]`, keyed parents) is required migration work on the existing script, not a greenfield build.

## Cross-Story Dependencies

- Story 1.2's config schema (notably `status_map` and `done_statuses`) defines the vocabulary Story 1.1's adapter must emit valid `status` values against — design the schema and the adapter's status handling together.
- The manifest shape and config schema fixed here are the sole interface Epics 2–4 build on: the engine (Epics 2–3) consumes only this manifest, and Epic 4's `--init` scaffolds exactly this config schema. Changing either shape later ripples across all downstream epics.
