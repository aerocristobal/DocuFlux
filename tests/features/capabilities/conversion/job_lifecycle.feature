@conversion @p1
Feature: Managing a conversion job after submission
  In order to stay in control of work I have already started
  As a self-hoster watching the job list
  I want to cancel, retry and delete jobs, and to see a list that reflects reality

  Background:
    Given I am browsing with a session

  Scenario: Cancelling a job revokes its task
    Given a job in progress at 40 percent
    When I cancel that job
    Then the task is revoked
    And the job metadata is set to expire in 10 minutes

  Scenario: Deleting a job removes its stored output
    Given a completed job graded "good"
    When I delete that job
    Then the job's stored files are removed
    And the job metadata is removed

  Scenario: Retrying a job whose upload has been cleaned up is refused
    Given a completed job whose uploaded file has been cleaned up
    When I retry that job
    Then the retry is refused as a bad request

  Scenario: The job list prunes entries whose job has expired
    # History entries outlive the job hashes they point at, so a list that did not
    # prune them would show phantom jobs forever.
    Given my session history references a job that no longer exists
    When I ask the web UI for my job list
    Then the list is empty
    And the stale entry is pruned from my history

  Scenario Outline: A malformed job id is rejected before any Redis call
    # Job ids reach secure_filename and the filesystem, so they are validated first.
    When I <action> the job "not-a-uuid"
    Then the request is rejected as a bad request
    And no job metadata is read

    Examples:
      | action |
      | cancel |
      | delete |
      | retry  |
