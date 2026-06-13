# Story 3.1 — Detect Pandoc partial/empty output
# Epic 3 · Priority P0 · Status: ready
# Source: docs/BACKLOG.md (Story 3.1)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic3_reliability @story_3_1 @p0
Feature: Detect Pandoc partial/empty output

  Scenario: Pandoc writes a 0-byte file but exits 0
    Given a malformed document submitted for Pandoc conversion
    When Pandoc exits successfully but produces an empty output file
    Then the job status is "failed" with reason "empty_output"
