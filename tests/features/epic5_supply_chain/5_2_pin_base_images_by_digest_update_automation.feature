# Story 5.2 — Pin base images by digest + update automation
# Epic 5 · Priority P1 · Status: ready
# Source: docs/BACKLOG.md (Story 5.2)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic5_supply_chain @story_5_2 @p1
Feature: Pinned base images

  Scenario: Every base image is digest-pinned (happy path)
    Given the three service Dockerfiles
    When their FROM lines are inspected
    Then each base image is pinned by @sha256:... digest

  Scenario: Build is reproducible across runs (boundary)
    Given a digest-pinned Dockerfile
    When the image is built twice from the same context
    Then both builds resolve the identical base layer

  Scenario: Automation proposes digest bumps (alternative path)
    Given Dependabot/Renovate is configured
    When a newer base digest is published upstream
    Then a PR is opened proposing the digest bump
