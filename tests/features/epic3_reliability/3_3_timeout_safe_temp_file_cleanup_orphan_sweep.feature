# Story 3.3 — Timeout-safe temp file cleanup + orphan sweep
# Epic 3 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 3.3)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic3_reliability @story_3_3 @p1
Feature: Timeout-safe temp file cleanup

  Scenario: Temp files removed on normal completion (happy path)
    Given a conversion task that creates temp files
    When the task completes successfully
    Then its temp directory is removed

  Scenario: No temp files remain after a hard-timeout kill (boundary)
    Given a conversion task in progress with temp files on disk
    When the task is killed by a hard timeout
    Then no temp files remain after the next maintenance sweep

  Scenario: Orphan sweep reclaims pre-existing leaked files (alternative path)
    Given orphaned temp directories from a prior crash
    When the beat-scheduled orphan sweep runs
    Then the orphaned directories are deleted

  Scenario: Sweep never deletes the source tree (error guard)
    Given a maintenance sweep with a misconfigured path
    When the sweep runs
    Then it refuses to delete anything outside the temp roots
