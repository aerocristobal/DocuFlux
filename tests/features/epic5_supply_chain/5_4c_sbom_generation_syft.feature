# Story 5.4c — SBOM generation (syft)
# Epic 5 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 5.4c)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic5_supply_chain @story_5_4c @p1
Feature: SBOM artifact

  Scenario: SBOM attached to a release (happy path)
    Given a release build
    When the CI SBOM job runs
    Then a syft-generated SBOM artifact is attached to the release
