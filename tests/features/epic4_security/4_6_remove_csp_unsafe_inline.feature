# Story 4.6 — Remove CSP unsafe-inline
# Epic 4 · Priority P2 · Status: ready
# Source: docs/BACKLOG.md (Story 4.6)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic4_security @story_4_6 @p2
Feature: Remove CSP unsafe-inline

  Scenario: Response CSP no longer allows unsafe-inline (happy path)
    Given the application is serving pages
    When a page response is inspected
    Then its Content-Security-Policy header does not contain unsafe-inline

  Scenario: Inline scripts and styles still function (boundary)
    Given inline scripts/styles moved to static files or nonce-tagged
    When the page loads under the stricter CSP
    Then all scripts and styles execute without CSP violations

  Scenario: SocketIO live updates still work (alternative path)
    Given the stricter CSP
    When a conversion job streams status over SocketIO
    Then the client receives live updates without CSP errors

  Scenario: An injected inline script is blocked (error/security path)
    Given the stricter CSP without unsafe-inline
    When an inline <script> is injected into the page
    Then the browser refuses to execute it
