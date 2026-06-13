# Story 2.1a — CPU image: Tesseract + language packs
# Epic 2 · Priority P0 · Status: ready
# Source: docs/BACKLOG.md (Story 2.1a)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic2_ocr_capability @story_2_1a @p0
Feature: CPU worker image with Tesseract

  Scenario: Built CPU image contains Tesseract (happy path)
    Given the CPU worker image is built
    When "tesseract --version" runs inside the container
    Then it reports an installed Tesseract version

  Scenario: Configured language packs are present (alternative path)
    Given the image built with CJK language packs configured
    When the available OCR languages are listed
    Then the configured languages are present

  Scenario: GPU image is unaffected (boundary)
    Given the GPU worker image build
    When it is built
    Then it builds successfully without the CPU OCR layer breaking it
