# Urgent Security Fixes

This document outlines the user stories for remaining security vulnerabilities identified by GitHub CodeQL scans.

## Epic: Security Remediation from CodeQL Scans (Round 2)

### Story 1: Remediate Incomplete URL Sanitization

*   **As a** developer,
*   **I want** to use a robust URL parsing and validation method in `worker/tasks.py`,
*   **So that** I can prevent incomplete URL substring sanitization vulnerabilities (CWE-020) when checking for redirects to login pages.

**Acceptance Criteria:**
*   The check for `signin.amazon.com` in `worker/tasks.py` should parse the URL and check the hostname, rather than using a simple string `in` check.
*   The CodeQL alert `py/incomplete-url-substring-sanitization` (alert #50) is resolved.

### Story 2: Remediate Stack Trace Exposure

*   **As a** developer,
*   **I want** to avoid exposing raw exception stack traces to users in `web/app.py`,
*   **So that** I can prevent information exposure (CWE-209) that could aid attackers.

**Acceptance Criteria:**
*   Generic error messages should be returned to the user in case of server-side exceptions.
*   Detailed stack traces should be logged on the server for debugging but not sent in HTTP responses.
*   The CodeQL alert `py/stack-trace-exposure` (alert #30) is resolved.