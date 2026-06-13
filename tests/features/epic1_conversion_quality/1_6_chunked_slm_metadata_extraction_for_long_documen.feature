# Story 1.6 — Chunked SLM metadata extraction for long documents
# Epic 1 · Priority P2 · Status: ready
# Source: docs/BACKLOG.md (Story 1.6)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic1_conversion_quality @story_1_6 @p2
Feature: Chunked SLM metadata extraction

  Scenario: Long document samples head and tail, not just the opening (happy path)
    Given a document longer than the SLM context limit
    When metadata extraction runs
    Then content from both the start and the end of the document informs the metadata

  Scenario: Short document is processed without chunking (alternative path)
    Given a document within the SLM context limit
    When metadata extraction runs
    Then the full content is used directly with no sampling

  Scenario: Total latency stays bounded on a very long document (boundary)
    Given a 50+ page document
    When chunked extraction runs
    Then total extraction latency stays within the configured bound

  Scenario: Better metadata than naive truncation (business rule)
    Given a long document whose key title appears only past the first 2000 words
    When chunked extraction runs
    Then the extracted title reflects that later content
