# Story 4.2 — Explicit rate limit on /api/v1/convert
# Epic 4 · Priority P0 · Status: ready
# Source: docs/BACKLOG.md (Story 4.2)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic4_security @story_4_2 @p0
Feature: Explicit rate limit on /api/v1/convert

  Scenario: Convert endpoint enforces its rate limit
    Given the per-key convert limit is N requests/min
    When a client sends N+1 convert requests within a minute
    Then the N+1th response is HTTP 429
