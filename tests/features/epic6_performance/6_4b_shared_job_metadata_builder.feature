# Story 6.4b — Shared job-metadata builder
# Epic 6 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 6.4b)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic6_performance @story_6_4b @p1
Feature: Shared job-metadata builder

  Scenario: Conversion and capture use one builder (happy path)
    Given the conversion and capture routes
    When they construct job metadata
    Then both call the shared builder in shared/job_metadata.py

  Scenario: Builder output matches prior dicts (boundary)
    Given the same inputs the old inline code received
    When the shared builder runs
    Then it produces an equivalent metadata structure
