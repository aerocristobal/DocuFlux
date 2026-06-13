# Story 3.2 — Fix worker healthcheck target
# Epic 3 · Priority P0 · Status: ready
# Source: docs/BACKLOG.md (Story 3.2)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic3_reliability @story_3_2 @p0
Feature: Worker healthcheck targets Celery

  Scenario: Healthy worker reports healthy (happy path)
    Given a running worker with a responsive Celery process
    When the container healthcheck runs
    Then the container is reported healthy

  Scenario: Dead Celery flips the container to unhealthy (error path)
    Given a worker whose Celery process has died
    When the container healthcheck runs
    Then the container is reported unhealthy
    And the orchestrator restarts the worker

  Scenario: Healthcheck no longer depends on the MCP endpoint (boundary)
    Given the MCP server is unreachable
    When the worker healthcheck runs against a healthy Celery
    Then the worker is still reported healthy
