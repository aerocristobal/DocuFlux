# Story 6.2 — Eager Marker model warmup
# Epic 6 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 6.2)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic6_performance @story_6_2 @p1
Feature: Eager Marker model warmup

  Scenario: First conversion pays no model-load penalty (happy path)
    Given a freshly started GPU worker with warmup enabled
    When the first Marker conversion is submitted
    Then it does not pay a model-loading penalty

  Scenario: Warmup disabled preserves lazy load (alternative path)
    Given a worker started with warmup disabled
    When the first Marker conversion is submitted
    Then the model loads lazily on first use

  Scenario: Healthcheck reports warm/cold state (boundary)
    Given a worker with warmup enabled
    When the healthcheck (3.2) is queried during warmup
    Then it reports a cold/warming state until models are loaded
