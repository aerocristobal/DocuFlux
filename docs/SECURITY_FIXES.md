# Security Remediation Plan

This document outlines the user stories to remediate security vulnerabilities identified by GitHub CodeQL scans.

## Epic: Security Remediation from CodeQL Scans

### Story 1: Sanitize Format Strings in MCP Server

*   **As a** developer,
*   **I want** to sanitize user-provided input in `console.log` calls in the `mcp_server/server.js` file,
*   **So that** I can prevent tainted format string vulnerabilities (CWE-134) and avoid garbled or misleading log output.

**Acceptance Criteria:**
*   All `console.log` statements in `mcp_server/server.js` that include variables should use format specifiers (e.g., `%s`) instead of string concatenation.
*   The CodeQL alerts `js/tainted-format-string` (alerts #51 and #52) are resolved.

### Story 2: Improve URL Sanitization

*   **As a** developer,
*   **I want** to use a robust URL parsing and validation method in `worker/tasks.py`,
*   **So that** I can prevent incomplete URL substring sanitization vulnerabilities (CWE-020) when checking for redirects to login pages.

**Acceptance Criteria:**
*   The check for `signin.amazon.com` in `worker/tasks.py` should parse the URL and check the hostname, rather than using a simple string `in` check.
*   The CodeQL alert `py/incomplete-url-substring-sanitization` (alert #50) is resolved.

### Story 3: Prevent Path Injection Vulnerabilities

*   **As a** developer,
*   **I want** to validate and sanitize all user-controlled data used in file paths,
*   **So that** I can prevent path injection vulnerabilities (CWE-022) across `web/app.py`, `web/encryption.py`, and `worker/encryption.py`.

**Acceptance Criteria:**
*   All file paths constructed from user-provided data must be validated to ensure they are within the expected base directory.
*   The `werkzeug.utils.secure_filename` function or a similar robust method should be used to sanitize filenames.
*   The CodeQL alerts `py/path-injection` (alerts #23-25, #31-49) are resolved.

### Story 4: Avoid Stack Trace Exposure

*   **As a** developer,
*   **I want** to avoid exposing raw exception stack traces to users in `web/app.py`,
*   **So that** I can prevent information exposure (CWE-209) that could aid attackers.

**Acceptance Criteria:**
*   Generic error messages should be returned to the user in case of server-side exceptions.
*   Detailed stack traces should be logged on the server for debugging but not sent in HTTP responses.
*   The CodeQL alert `py/stack-trace-exposure` (alert #30) is resolved.

### Story 5: Prevent Logging of Sensitive Data

*   **As a** developer,
*   **I want** to ensure that sensitive data like secrets and keys are not logged in clear text,
*   **So that** I can prevent sensitive information disclosure (CWE-532) in `web/secrets.py` and `worker/secrets.py`.

**Acceptance Criteria:**
*   All logging statements that might include sensitive information should be reviewed and modified to redact or exclude the sensitive parts.
*   The CodeQL alerts `py/clear-text-logging-sensitive-data` (alerts #26-29) are resolved.