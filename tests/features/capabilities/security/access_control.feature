@security @p0
Feature: Keeping one caller's documents away from another
  In order to run DocuFlux without leaking what my colleagues are converting
  As the operator of a shared deployment
  I want listings scoped to their owner and admin views behind a credential

  Scenario: An unidentified caller sees no captures
    # This endpoint used to return the global index to anyone who asked, exposing
    # every user's captured document titles and source URLs.
    Given captures exist belonging to another client
    When an anonymous caller lists captures
    Then no captures are returned
    And the global capture index is never read

  Scenario: A client sees only its own captures
    Given captures exist belonging to client "alice"
    When client "alice" lists captures
    Then the captures belonging to "alice" are returned

  Scenario: One client cannot see another client's captures
    Given captures exist belonging to client "bob"
    When client "alice" lists captures
    Then no captures are returned

  Scenario: The operator can still see everything, with credentials
    Given captures exist belonging to another client
    When an admin lists all captures
    Then the captures are returned

  Scenario: The operator's view is not open to everyone
    When an anonymous caller lists all captures
    Then the request is rejected as unauthenticated

  Scenario: A browsing session sees the captures it made
    Given I am browsing with a session
    And captures exist belonging to my session
    When I list captures
    Then the captures belonging to my session are returned
