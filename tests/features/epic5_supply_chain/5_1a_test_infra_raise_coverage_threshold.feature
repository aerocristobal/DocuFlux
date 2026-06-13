# Story 5.1a — Test infra + raise coverage threshold
# Epic 5 · Priority P0 · Status: ready
# Source: docs/BACKLOG.md (Story 5.1a)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic5_supply_chain @story_5_1a @p0
Feature: Crypto test infrastructure and coverage gate

  Scenario: Security test scaffolding is available (happy path)
    Given the new tests/unit security suite scaffolding
    When a sub-card (5.1b–d) adds a test using the shared fixtures and KAT helpers
    Then the test can be collected and run by pytest

  Scenario: Coverage exclusions are removed as modules gain tests (alternative path)
    Given a module that previously appeared in .coveragerc omit
    When its tests land
    Then its omit entry is removed and it counts toward coverage

  Scenario: Raised threshold fails CI when coverage drops (error path)
    Given fail_under is raised to 80 after 5.1b-d merge
    When total coverage falls below 80 percent
    Then the CI coverage gate fails the build
