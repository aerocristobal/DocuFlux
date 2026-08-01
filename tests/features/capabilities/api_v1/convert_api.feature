@api_v1 @p0
Feature: Submitting conversions through the REST API
  In order to convert documents from a backend pipeline without a browser
  As an API integrator
  I want predictable authentication and validation semantics

  Background:
    Given the service has free disk space

  Scenario: A request without an API key is rejected
    When I submit "notes.md" to the API without a key
    Then the API rejects it as unauthenticated

  Scenario: A request with an unknown API key is forbidden
    When I submit "notes.md" to the API with an unrecognised key
    Then the API rejects it as forbidden

  Scenario: A valid key is accepted and the job is queued
    When I submit "notes.md" to the API converting to html
    Then the API accepts the submission
    And the response carries a job id and a status url

  Scenario: An unsupported output format is unprocessable
    When I submit "notes.md" to the API converting to "klingon"
    Then the API rejects it as unprocessable
    And the error mentions "klingon"

  Scenario: An unknown engine is unprocessable
    When I submit "notes.md" to the API with the engine "telepathy"
    Then the API rejects it as unprocessable
    And the error mentions "engine"

  Scenario: A missing output format is a bad request
    When I submit "notes.md" to the API with no output format
    Then the API rejects it as a bad request
    And the error mentions "to_format"

  Scenario: The input format is inferred from the file extension
    When I submit "notes.md" to the API converting to html
    Then the task is dispatched with the from_format "markdown"

  Scenario: Malformed pandoc options are a bad request
    When I submit "notes.md" to the API with the pandoc options "{not json"
    Then the API rejects it as a bad request
    And the error mentions "valid JSON"

  Scenario: Pandoc options are refused for engines that cannot use them
    When I submit "scan.pdf" to the API with the engine "marker" and pandoc options
    Then the API rejects it as unprocessable

  Scenario: Image extraction can be turned off
    When I submit "scan.pdf" to the API with the engine "marker" and images disabled
    Then the task options disable image extraction
