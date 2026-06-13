# Story 5.3a — Python lint+type gate (ruff, mypy)
# Epic 5 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 5.3a)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic5_supply_chain @story_5_3a @p1
Feature: Python lint and type gate

  Scenario: Clean code passes the gate (happy path)
    Given Python code with no ruff or mypy violations
    When the CI lint+type job runs
    Then the job passes

  Scenario: A lint violation fails CI in enforce mode (error path)
    Given a ruff violation is introduced
    When the CI lint job runs in enforce mode
    Then the job fails with the violation reported

  Scenario: Staged adoption starts in warn mode (boundary)
    Given the gate is first introduced
    When it runs in warn mode
    Then violations are reported but do not fail the build
