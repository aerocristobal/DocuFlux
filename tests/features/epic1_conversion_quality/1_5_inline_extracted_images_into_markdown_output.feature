# Story 1.5 — Inline extracted images into Markdown output
# Epic 1 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 1.5)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic1_conversion_quality @story_1_5 @p1
Feature: Inline extracted images into Markdown output

  Scenario: Extracted images resolve inside the downloaded zip (happy path)
    Given a PDF whose Marker conversion extracts images
    When the user downloads the zipped result
    Then the Markdown references images via relative images/... paths
    And every referenced image file exists in the archive

  Scenario: Text-only consumer omits images (alternative path)
    Given a conversion requested with the images-omitted option
    When the output is produced
    Then no image files are written and no image references appear

  Scenario: Document with no images produces no image folder (boundary)
    Given a text-only PDF with zero extractable images
    When conversion completes
    Then the output contains no images/ directory and no dangling references
