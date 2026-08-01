@storage @p1
Feature: Expiring converted output on a schedule
  In order to run DocuFlux as a converter rather than a document store
  As a self-hoster with finite disk
  I want outputs to expire predictably, and the API to say so

  # The retention decision function itself is pinned in
  # tests/characterization/test_retention_decisions.py. These scenarios cover what a
  # caller can actually observe.

  Scenario: A downloaded job records when it was collected
    Given a completed job with output on disk
    When I download it
    Then the job records that it was downloaded

  Scenario: Output that has expired reports gone rather than missing
    Given a completed job whose output has been cleaned up
    When I download it
    Then the API reports the output is gone

  Scenario: A failed job's metadata is kept only briefly
    Given a job that failed
    When its metadata expiry is set
    Then the metadata expires in 10 minutes

  Scenario: A successful job's metadata outlives a failed one
    Given a job that succeeded
    When its metadata expiry is set
    Then the metadata expires in 2 hours
