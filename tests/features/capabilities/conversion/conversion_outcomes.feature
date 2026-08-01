@conversion @api_v1 @p0
Feature: Reporting the outcome and quality of a conversion
  In order to trust converted output without opening every file
  As an API integrator feeding documents into a downstream pipeline
  I want a degraded conversion to be distinguishable from a good one

  Background:
    Given I am browsing with a session

  Scenario: A good conversion reports its quality grade
    Given a completed job graded "good"
    When I ask the API for its status
    Then the status is "success"
    And the response carries the quality grade "good"

  Scenario: A degraded conversion is flagged rather than reported as a plain success
    Given a completed job graded "poor"
    When I ask the API for its status
    Then the status is "completed-with-warnings"
    And the response carries the quality grade "poor"

  Scenario: The web UI still reports a degraded job as a plain success
    # A deliberate fork between the two surfaces: /api/v1/status distinguishes a
    # degraded conversion, /api/jobs does not. Pinned so the inconsistency stays a
    # decision rather than becoming a surprise.
    Given a completed job graded "poor"
    When I ask the web UI for my job list
    Then the job is listed with status "SUCCESS"

  Scenario: A failed conversion reports its error
    Given a failed job with the error "Pandoc failed"
    When I ask the API for its status
    Then the status is "failure"
    And the response carries the error "Pandoc failed"

  Scenario: A job still running reports progress rather than an outcome
    Given a job in progress at 40 percent
    When I ask the API for its status
    Then the status is "processing"
    And the response reports 40 percent progress

  Scenario: An unknown job is not found
    When I ask the API for the status of an unknown job
    Then the API reports the job was not found

  Scenario: A malformed job id is rejected before any lookup
    When I ask the API for the status of "not-a-uuid"
    Then the API rejects it as a bad request

  Scenario: A path traversal attempt never reaches the handler
    # Slashes stop it matching the route at all, so it is refused before the
    # job-id validator even runs.
    When I ask the API for the status of a traversal path
    Then the API reports the job was not found
