@conversion @p0
Feature: Submitting documents for conversion
  In order to get documents converted without babysitting the process
  As a self-hoster running DocuFlux for my team
  I want submissions to be accepted, validated, and tracked against my session

  Background:
    Given the service has free disk space

  Scenario: A submitted document is queued and returns a job id
    Given I am browsing with a session
    When I submit "notes.md" converting markdown to html
    Then the submission is accepted
    And I receive 1 job id
    And 1 conversion task is dispatched

  Scenario: Each file in a multi-file submission gets its own job
    Given I am browsing with a session
    When I submit 3 markdown files converting markdown to html
    Then the submission is accepted
    And I receive 3 job ids
    And 3 conversion tasks are dispatched

  Scenario: A file whose extension contradicts the declared format is rejected
    Given I am browsing with a session
    When I submit "notes.txt" converting markdown to html
    Then the submission is rejected as a bad request
    And the error mentions "mismatch"

  Scenario: The .markdown extension is accepted for the markdown format
    Given I am browsing with a session
    When I submit "notes.markdown" converting markdown to html
    Then the submission is accepted

  Scenario: Submissions are refused when the server is out of storage
    Given the service has no free disk space
    When I submit "notes.md" converting markdown to html
    Then the submission is refused for lack of storage

  Scenario: A submitted job appears in my own job list
    Given I am browsing with a session
    When I submit "notes.md" converting markdown to html
    Then the job is recorded in my session history

  Scenario: Another browsing session cannot see my jobs
    Given I am browsing with a session
    And I submit "notes.md" converting markdown to html
    When a different session asks for its job list
    Then that session sees no jobs
