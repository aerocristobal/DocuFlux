# User Instructions

- **CLAUDE.md Bloat Prevention:** Before adding ANY content to CLAUDE.md (global or project), run a size check first. If the file exceeds **150 lines or 8KB**, you MUST extract existing content to a skill or docs before adding new content. Never just append to a bloated CLAUDE.md.
- Never use the WebFetch tool. Always use curl via Bash for fetching URLs.
- **Always maximize parallelization.** See `rules/parallel-execution.md` for mandatory parallel execution patterns. Sequential execution of independent tasks is a VIOLATION.

## Motes

This project uses motes for all planning, memory, and task tracking. Knowledge is stored in `.memory/`.

**Do NOT use** markdown files, TodoWrite, TaskCreate, or external issue trackers for tracking work.

### Session Start

***Run `mote prime` at the start of every session for scored, relevant context.***

Prime outputs: active tasks, recent decisions, lessons, explores, echoes, and contradiction alerts. It auto-parses your git branch as keywords.

Focus priming on a topic: `mote prime <topic>`
Inspect a surfaced mote: `mote show <id>`

### Mid-Session Retrieval

When you need context beyond what prime surfaced:

| Need | Command | Example |
|------|---------|---------|
| Graph traversal | `mote context <topic>` | `mote context authentication` |
| Full-text search | `mote search <query>` | `mote search "retry logic"` |
| Reference docs | `mote strata query <topic>` | `mote strata query scoring` |
| Dependency chain view | `mote context --planning <id>` | `mote context --planning proj-t1abc` |

### Task Tracking & Planning

Find available work:

    mote ls --ready           # Tasks with no unfinished blockers
    mote pulse                # Active tasks sorted by weight

Create tasks with dependency links:

    mote add --type=task --title="Summary" --tag=topic --body "What and why"
    mote link <story-id> depends_on <epic-id>
    mote update <id> --status=completed

### Capturing Knowledge

Capture when you encounter:

| Trigger | Type | Command |
|---------|------|---------|
| Non-obvious choice made | decision | `mote add --type=decision --title="Summary" --tag=topic --body "Rationale"` |
| Gotcha or surprise discovered | lesson | `mote add --type=lesson --title="Summary" --tag=topic --body "Details"` |
| Researched alternatives | explore | `mote add --type=explore --title="Summary" --tag=topic --body "Findings"` |
| Quick thought | (auto) | `mote quick "your sentence here"` |

After capturing, link related motes: `mote link <id1> relates_to <id2>`
Give feedback on surfaced motes: `mote feedback <id> useful` or `mote feedback <id> irrelevant`

**Tag strategy:** Rare, specific tags beat generic ones.

### Session End

Run `mote session-end` for access flush and maintenance suggestions.

Run `mote dream` periodically for automated maintenance. Review with `mote dream --review`.

## Project Overview

**DocuFlux** is a containerized document conversion service combining Pandoc (universal converter) with Marker AI (deep learning PDF processor). It uses a microservices architecture with asynchronous task processing.

**Core Pattern**: Web UI (Flask) → Task Queue (Redis/Celery) → Worker (Pandoc + Marker AI) → Shared Volume Storage

### Critical Rules

1. **Always update github issues in the same commit** as the implementation
2. **Use Behavior Driven Design format** for issues
3. **Include clear and specific Definition of Done** for issues
4. **Use consistent formatting** - follow the three-tier approach (A/B/C)
5. **Document what changed, not just what was done** - include files, line counts, verification
6. **Validate Definition of Done** for each issues before marking it closed

## Essential Commands

```bash
# Build and start all services
docker-compose up --build

# GPU build
./scripts/build.sh auto
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up

# CPU-only build
./scripts/build.sh cpu
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up

# Logs
docker-compose logs -f web|worker|redis|beat

# Stop / rebuild
docker-compose down
docker-compose up --build worker

# Tests
pytest -v
pytest --cov=web --cov=worker --cov-report=term-missing

# Syntax check
python3 -m py_compile web/app.py
python3 -m py_compile worker/tasks.py worker/warmup.py
```

## Skills Reference

| Skill | Use When |
|-------|----------|
| `docuflux-architecture` | Working on architecture, service communication, Redis keys, file paths, environment config |
| `docuflux-dev-patterns` | Implementing features, following coding patterns, working with the build system |
| `docuflux-troubleshooting` | Debugging, troubleshooting, running tests, investigating known issues |

## References

- **README.md**: User-facing documentation, feature overview
- **BUILD.md**: Build system documentation, GPU/CPU profiles, deployment
- **docs/**: Additional documentation (deployment, API, formats, troubleshooting)
