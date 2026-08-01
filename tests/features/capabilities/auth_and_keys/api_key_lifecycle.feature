@auth @p1
Feature: Issuing and revoking API keys
  In order to let backend systems call DocuFlux without sharing one static secret
  As the operator of a DocuFlux deployment
  I want to mint, scope and revoke keys behind an admin credential

  Scenario: Minting a key requires the admin credential
    When I ask for a new key without admin credentials
    Then the request is rejected as unauthenticated

  Scenario: A non-Bearer authorization header is not admin credentials
    When I ask for a new key with the header "Basic abc123"
    Then the request is rejected as unauthenticated

  Scenario: The wrong admin secret is forbidden
    When I ask for a new key with the wrong admin secret
    Then the request is rejected as forbidden

  Scenario: A minted key is prefixed so it is recognisable in logs and configs
    When I mint a key labelled "ci-pipeline"
    Then a key is issued
    And the key begins with "dk_"

  Scenario: A minted key carries its label and an expiry
    When I mint a key labelled "ci-pipeline"
    Then the stored key records the label "ci-pipeline"
    And the stored key records an expiry

  Scenario: The caller can shorten a key's lifetime
    When I mint a key that expires in 7 days
    Then the stored key expires in 7 days

  Scenario Outline: A nonsensical lifetime is refused
    When I mint a key that expires in <days> days
    Then the request is rejected as a bad request

    Examples:
      | days |
      | 0    |
      | -5   |

  Scenario: Revoking a key that was never issued reports not found
    When I revoke a key that does not exist
    Then the key is reported as not found
