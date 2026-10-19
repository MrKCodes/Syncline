# Wiring the auto-sync Stop hook

To keep a repo's project board current automatically, add a **Stop hook** to that
repo's `.claude/settings.json` that runs `syncline_sync.py` on every Claude Code
Stop. The sync is sha256-cached and never throws, so an unchanged plan is a silent
no-op and a failure can't block the session.

## The hook

Use **absolute paths** (the hook runs from the target repo's cwd, not Syncline):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/Syncline/bin/syncline_sync.py --config /path/to/Syncline/configs/<name>.config.json </dev/null 2>/dev/null || true",
            "timeout": 90,
            "statusMessage": "Syncing plan to the project board…"
          }
        ]
      }
    ]
  }
}
```

- `</dev/null` — a Stop hook may pass JSON on stdin; the script doesn't read it, so
  redirect to avoid any blocking read.
- `2>/dev/null || true` — belt-and-braces; the script already always exits 0, but
  this guarantees a stray stderr line never looks like a failure.
- `timeout: 90` — generous headroom for a cold GraphQL round-trip; a no-op returns
  in well under a second.

## Install it with the update-config skill

If you have the Claude Code `update-config` skill, ask it to add the Stop hook
above to the target repo's `.claude/settings.json` (it reads-then-merges, so it
won't clobber existing hooks). Otherwise edit the file by hand and merge into any
existing `hooks.Stop` array.

## Caveats

- **New settings file → reload once.** If `.claude/settings.json` didn't exist when
  the session started, the config watcher won't pick it up until you open `/hooks`
  once (or restart). The hook is written correctly regardless.
- **Local-only.** If the target repo gitignores `.claude/`, the hook (and
  Syncline's state under `configs/state/`) won't travel to teammates or CI. That's
  intended for a personal workflow; make it a committed, team-shared setup only if
  everyone has `gh` authed with the `project` scope and shares the state.
- **It only fires work when the plan changed.** The sync keys off a sha256 of
  `plan_file`; editing other files won't trigger a board write.
