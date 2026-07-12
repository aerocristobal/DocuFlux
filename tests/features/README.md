# BDD Feature Files

Executable acceptance criteria for the DocuFlux improvement backlog, exported as
Gherkin `.feature` files from the kanban board.

**Scope of this export:** the **ready** (unblocked) cards across the **P0**, **P1**,
and **P2** priority bands. Blocked cards (status `todo`) are not yet exported — they
already carry BDD scenarios on the board and will be exported as their dependencies
complete and they move to `ready`.

Each feature corresponds to one user story in [`docs/user-stories/BACKLOG.md`](../../docs/user-stories/BACKLOG.md)
and follows the BDD structure from the team's
`TDD-BDD-User-Story-Template.md` reference (one scenario → one behavior; declarative,
business-language steps; present-tense `Then`; happy / alternative / boundary / error
coverage).

## Layout

```
tests/features/
├── epic1_conversion_quality/
├── epic2_ocr_capability/
├── epic3_reliability/
├── epic4_security/
├── epic5_supply_chain/
└── epic6_performance/
```

One `.feature` file per story, named `<story_id>_<slug>.feature` (e.g.
`1_1_quality_scoring_of_converted_markdown.feature`).

## Tags

Every `Feature` carries three tags so you can filter runs:

- `@epicN_<name>` — the epic (e.g. `@epic3_reliability`)
- `@story_<id>` — the story (e.g. `@story_3_5`)
- `@p0` / `@p1` / `@p2` — the priority band

Example filtered runs (once a runner is wired up):

```bash
# pytest-bdd (via -k / markers) or behave tags:
behave --tags=@p0                 # only P0 acceptance tests
behave --tags=@epic4_security     # only security stories
behave --tags=@story_1_1          # a single story
```

## Status: specifications, not yet executable

These files are the **acceptance specification**. No BDD runner or step
definitions exist in the repo yet. To make them executable, pick a runner and add
step definitions:

- **behave** (Pythonic, natural for this codebase): `pip install behave`, put step
  defs under `tests/features/steps/`.
- **pytest-bdd** (integrates with the existing pytest suite): `pip install pytest-bdd`,
  bind scenarios in `tests/` test modules.

Per the template's nesting model, each Gherkin step drives an inner TDD
Red-Green-Refactor cycle; the per-story **TDD Implementation Map** on each kanban
card lists the unit-under-test and required unit tests for the step definitions.

## Regenerating

These files are generated from the kanban cards' acceptance criteria. When a card's
Gherkin changes, regenerate rather than hand-editing, so the board and the
`.feature` files stay in sync.
