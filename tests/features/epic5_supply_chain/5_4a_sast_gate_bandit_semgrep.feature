# Story 5.4a — SAST gate (bandit/semgrep)
# Epic 5 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 5.4a)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic5_supply_chain @story_5_4a @p1
Feature: SAST gate

  Scenario: Clean code passes SAST (happy path)
    Given code with no high-severity SAST findings
    When the CI SAST job runs
    Then the job passes

  Scenario: A high-severity finding fails CI (error path)
    Given an introduced high-severity vulnerability
    When the SAST job runs
    Then the job fails with the finding reported
