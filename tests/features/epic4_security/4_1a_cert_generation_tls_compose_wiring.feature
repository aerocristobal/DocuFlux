# Story 4.1a — Cert generation + TLS compose wiring
# Epic 4 · Priority P0 · Status: ready
# Source: docs/BACKLOG.md (Story 4.1a)
# Generated from kanban card on 2026-06-13. Acceptance criteria — implement step defs to run.

@epic4_security @story_4_1a @p0
Feature: Redis TLS certificate generation and wiring

  Scenario: Cert script produces a usable CA and server certs (happy path)
    Given a clean deploy/certs directory
    When scripts/generate-redis-certs.sh is run
    Then a CA certificate and a server certificate/key are produced in deploy/certs

  Scenario: TLS compose profile brings up a TLS-listening Redis (alternative path)
    Given generated certs in deploy/certs
    When the stack is started with docker-compose.tls.yml
    Then Redis listens for TLS connections

  Scenario: Cert paths align with the client SSL settings (boundary)
    Given the generated cert file names
    When shared/redis_client.py reads ssl_ca_certs, ssl_certfile, and ssl_keyfile
    Then the configured paths point at the generated files
