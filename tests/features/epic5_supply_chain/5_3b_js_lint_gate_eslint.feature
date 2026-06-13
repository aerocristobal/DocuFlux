# Story 5.3b — JS lint gate (eslint)
# Epic 5 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 5.3b)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic5_supply_chain @story_5_3b @p1
Feature: JavaScript lint gate

  Scenario: Clean JS passes the gate (happy path)
    Given JS with no eslint violations
    When the CI eslint job runs
    Then the job passes

  Scenario: An eslint error fails CI in enforce mode (error path)
    Given an eslint error is introduced in extension-src/
    When the CI eslint job runs in enforce mode
    Then the job fails
