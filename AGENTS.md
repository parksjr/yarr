# AGENTS.md

## What this is

yarr turns a YouTube or Spotify link into MP3s with editable metadata and saves
them into a Plex music library. FastAPI backend + one vanilla-JS page (no
framework, no build step). Runs as a Docker container; default port 7734.

## Where things live

- app/main.py — FastAPI routes and job orchestration (fetch / preview / save)
- app/source.py — source detection + routing (YouTube / Spotify)
- app/youtube.py — yt-dlp download (audio -> MP3 + thumbnail)
- app/spotify.py — spotDL download (Spotify track/album/playlist -> tagged MP3s)
- app/chapters.py — chapter normalization + ffmpeg stream-copy split
- app/metadata.py — mutagen ID3 tag read/write + artist/title guessing
- app/library.py — destination path building + file placement
- app/jobs.py — in-memory Job / JobStore
- app/static/ — index.html, app.js, style.css (served as-is)
- Dockerfile, docker-compose.yml (local build), requirements.txt, entrypoint.sh
- .github/workflows/docker-publish.yml — multi-arch GHCR publish on push to main

## Run locally

    docker compose up -d --build    # http://localhost:7734

MUSIC_HOST_PATH in .env (gitignored) points the music mount. music/ is
gitignored for scratch saves.

## Gotchas

- No build step, but static files are COPYed into the image at build time.
  After editing app/static/\* you MUST `docker compose up -d --build`.
  A plain restart serves stale files.
- Container paths: WORKDIR is /app and the code is `COPY app ./app`, so files
  live at /app/app/... when you exec into the container to inspect.
- YouTube bot check: some videos (long compilations) need the JS challenge
  solver. Keep `yt-dlp-ejs` (requirements.txt) and the Deno install (Dockerfile)
  or those videos fail with "This video is not available".
- macOS Docker: do not bind-mount a host /tmp/... path for testing. Docker
  Desktop can bind to the Linux VM's /tmp instead of the Mac's, so files the
  container writes never appear on the host. Use a path under /Users (or the
  gitignored music/ dir).
- docker-compose.yml is the LOCAL build (build: ., image yarr:latest). Homelab
  deployments use `image: ghcr.io/parksjr/yarr:latest` with no build section
  (see README). Keep these straight: the local compose ignores a pulled GHCR
  image.
- JobStore is in-memory; jobs disappear on restart and staging dirs are only
  cleaned on a successful save, so abandoned downloads leak staging files.
- save_many and batch/save move files one at a time (validated up front). A
  mid-way failure can leave a partial save; it is not atomic.
- Frontend is stateful vanilla JS in app.js. Single / split / batch modes share
  one metadata form, so keep field IDs stable (f-artist, f-title, ...) and the
  sharedFieldMap mapping in sync when adding a field.

## Validate before committing

    python3.11 -m py_compile app/*.py     # code uses 3.10+ `X | None` syntax
    node --check app/static/app.js

No lint or test scripts exist in this repo. Manual/Playwright checks were done
ad hoc.

## Agent context

This repo may be reached from a parent workspace that holds several unrelated
apps and is sometimes driven by its own agent sessions. When that is the case:
yarr is its own git repo under yarr/, so scope git and code edits to yarr/
and never git init the parent. The parent also holds a
www.youtube.com_cookies.txt that yarr's local .env may reference.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:970c3bf2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
