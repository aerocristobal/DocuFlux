# Story 3.4 — SLM JSON parse failures surfaced, with one repair retry
# Epic 3 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 3.4)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic3_reliability @story_3_4 @p1
Feature: SLM JSON parse failure handling

  Scenario: Valid SLM JSON parses normally (happy path)
    Given the SLM returns well-formed JSON metadata
    When metadata extraction runs
    Then the metadata is stored and no degraded flag is set

  Scenario: Malformed JSON triggers one constrained re-prompt (alternative path)
    Given the SLM returns malformed JSON
    When metadata extraction parses the response
    Then the failure is logged
    And exactly one constrained re-prompt is attempted

  Scenario: Re-prompt also fails -> degraded flag, not silent fallback (error path)
    Given the re-prompt also returns unparseable JSON
    When extraction finishes
    Then the job metadata carries a metadata_degraded flag
    But the system does not silently emit an identical default as if it succeeded
