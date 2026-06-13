# Story 6.4a — Split long convert routes
# Epic 6 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 6.4a)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic6_performance @story_6_4a @p1
Feature: Route refactor (behavior-preserving)

  Scenario: Refactored convert route preserves behavior (happy path)
    Given the existing convert and api_v1_convert routes
    When they are split into validate/enqueue/respond helpers
    Then the API responses are byte-identical to before for the same inputs

  Scenario: Each helper is unit-testable in isolation (boundary)
    Given the extracted helpers
    When each is called directly with crafted inputs
    Then it can be tested without spinning up the full request
