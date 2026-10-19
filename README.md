# Syncline

Reusable developer-support tooling, extracted so it can serve any repo (not tied
to the project it was first built in).

## Syncline — plan ↔ GitHub Project sync

**Syncline** keeps a BMAD-style plan (epics, stories, sprint status) and a GitHub
**Project (v2)** board in alignment. Epics and stories become real repo issues with
native **sub-issue** links (epic → its stories), a single-select **Status**, and a
custom **Work Type** (Epic/Story) field. One importer seeds the board; one sync
keeps it current and is safe to run on every Claude Code Stop.

> *Syncline* (geology): a trough-shaped fold where rock layers sit in alignment —
> and it contains "sync". The tool keeps two layers (your plan ↔ the board) aligned.

### Quick start

```bash
# 0. One-time: authenticate gh WITH the project scope (Projects v2 is GraphQL-only).
gh auth login -s project -h github.com
gh auth status            # confirm 'project' appears in the scopes

# 1. Make a config for your repo (every field is documented inline).
mkdir -p configs/state
cp configs/example.config.json configs/myrepo.config.json
$EDITOR configs/myrepo.config.json

# 2. Build the manifest from the BMAD plan.
python3 bin/syncline_build_manifest.py --config configs/myrepo.config.json

# 3. Dry-run, then import.
python3 bin/syncline_import.py --config configs/myrepo.config.json --dry-run
python3 bin/syncline_import.py --config configs/myrepo.config.json

# 4. Sync on demand (and wire a Stop hook — see docs/HOOK.md).
python3 bin/syncline_sync.py --config configs/myrepo.config.json
```

### Layout

```
Syncline/
  AGENTS.md                       # agent orientation (read first in a fresh session)
  README.md                       # this file
  bin/
    syncline_build_manifest.py    # BMAD plan -> manifest JSON
    syncline_import.py            # one-time idempotent board import (--dry-run)
    syncline_sync.py              # incremental reconcile; Stop-hook entry point
  configs/
    example.config.json           # documented template; copy per repo
    state/                        # Syncline bookkeeping (manifest/import-state/cache) — gitignored
  docs/
    USAGE.md                      # end-to-end walk-through + verification snippets
    HOOK.md                       # wiring the auto-sync Stop hook
  .claude/skills/project-sync/
    SKILL.md                      # the authoritative operating guide for an agent
```

### Requirements
- `gh` CLI, authed with the **`project`** scope.
- `python3` (standard library only — no third-party packages to install).

See `.claude/skills/project-sync/SKILL.md` for the full operating guide and the
hard-won gotchas (GraphQL `Int!` typing, the reserved "Type" field name, local-only
state, rollup verification).
