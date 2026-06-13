# Story 1.1 — Quality scoring of converted Markdown
# Epic 1 · Priority P0 · Status: ready
# Source: docs/BACKLOG.md (Story 1.1)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic1_conversion_quality @story_1_1 @p0
Feature: Quality scoring of converted Markdown

  Scenario: Scanned PDF through Pandoc yields 3 words per page
    Given a scanned PDF converted via the Pandoc engine
    When quality scoring runs on the output
    Then the job carries quality grade "poor" with reason code "low_word_density"
