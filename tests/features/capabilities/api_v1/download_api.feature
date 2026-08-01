@api_v1 @storage @p1
Feature: Retrieving converted output
  In order to collect the result of a conversion I submitted
  As an API integrator
  I want downloads that are authenticated and honest about what is available

  Scenario: Downloading requires an API key
    Given a completed job with output on disk
    When I download it without a key
    Then the API rejects it as unauthenticated

  Scenario: A job that has not finished has nothing to download
    Given a job in progress at 40 percent
    When I download it
    Then the API reports the job was not found
    And the error mentions "not completed"

  Scenario: Output that has already been cleaned up reports gone
    # Retention is deliberately short. A 410 tells the caller the job succeeded but
    # its output has expired, which a 404 would not.
    Given a completed job whose output has been cleaned up
    When I download it
    Then the API reports the output is gone

  Scenario: A single-file result downloads as that file
    Given a completed job with output on disk
    When I download it
    Then I receive the converted file

  Scenario: Marker's metadata sidecar is never served as the result
    # metadata.json sits alongside the output; serving it would hand the caller
    # internals instead of their document.
    Given a completed job whose output directory also holds metadata.json
    When I download it
    Then I receive the converted file
    And the response is not the metadata sidecar
