@capture @p1
Feature: Capturing a document from an authenticated web reader
  In order to turn a paywalled or authenticated reader into a document I can keep
  As an extension user working through a long online book
  I want a capture session that accepts pages reliably and refuses nonsense

  Scenario: Starting a session allocates a job to track it
    When I start a capture session titled "Chapter One"
    Then the session is created
    And it reports a session id and a job id
    And it reports the page limit

  Scenario: An unrecognised output format falls back to Markdown
    When I start a capture session requesting the "klingon" format
    Then the session is created
    And the session records the "markdown" format

  Scenario: Pages cannot be added to a session that has finished
    Given a capture session that has already finished
    When I submit a page to it
    Then the page is refused as a conflict

  Scenario: Pages cannot be added to a session that never existed
    Given no such capture session
    When I submit a page to it
    Then the session is reported as not found

  Scenario: Re-sending the same page does not duplicate it
    # The extension retries on flaky connections, so the same page arrives twice.
    Given an active capture session
    And page 1 has already been captured
    When I submit page 1 again
    Then the page is accepted
    And the page is not stored a second time

  Scenario: A session cannot grow past the page limit
    Given an active capture session that has reached the page limit
    When I submit a page to it
    Then the page is refused as unprocessable

  Scenario: Finishing a session with nothing captured is refused
    Given an active capture session
    When I finish the session
    Then finishing is refused as unprocessable

  Scenario: Finishing a session with pages queues assembly
    Given an active capture session
    And 3 pages have been captured
    When I finish the session
    Then the session is finished
    And an assembly task is queued
