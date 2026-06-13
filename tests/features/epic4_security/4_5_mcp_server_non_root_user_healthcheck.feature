# Story 4.5 — MCP server: non-root user + healthcheck
# Epic 4 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 4.5)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic4_security @story_4_5 @p1
Feature: MCP server container posture

  Scenario: Container runs as non-root (happy path)
    Given the MCP server image is built
    When a container is started from it
    Then the main process runs as a non-root user

  Scenario: Healthcheck reports healthy when MCP is up (alternative path)
    Given a running MCP container
    When the orchestrator runs its HEALTHCHECK
    Then the container is reported healthy

  Scenario: Playwright still functions under the non-root user (boundary)
    Given the non-root MCP container
    When a Playwright-driven capture runs
    Then it completes without sandbox/permission errors

  Scenario: Dead MCP is reported unhealthy (error path)
    Given the MCP process has crashed
    When the HEALTHCHECK runs
    Then the container is reported unhealthy
