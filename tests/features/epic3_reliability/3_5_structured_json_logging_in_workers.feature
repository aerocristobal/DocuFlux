# Story 3.5 — Structured JSON logging in workers
# Epic 3 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 3.5)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic3_reliability @story_3_5 @p1
Feature: Structured JSON logging in workers

  Scenario: Worker emits JSON logs with correlation fields (happy path)
    Given a worker processes a conversion task
    When it logs an event
    Then the log line is valid JSON
    And it includes job_id and task_id correlation fields

  Scenario: Web and worker share one log format (alternative path)
    Given a request handled by the web tier and a task handled by a worker
    When both emit logs
    Then both use the format defined in shared/logging.py

  Scenario: Log config is centralized (boundary)
    Given the shared logging module
    When either tier initializes logging
    Then it consumes the shared config rather than a local duplicate
