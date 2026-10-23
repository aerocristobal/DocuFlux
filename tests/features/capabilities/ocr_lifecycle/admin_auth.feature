@ocr_lifecycle @p1 @pending
Feature: Only admins can control the AI engine
  In order to stop other users from tying up or freeing the GPU
  As a self-hoster sharing DocuFlux with other people
  I want loading, unloading and changing OCR settings to need the admin secret

  Scenario Outline: The one where the admin secret is missing
    When I try to <action> without the admin secret
    Then I am refused

    Examples:
      | action                  |
      | start the AI engine     |
      | stop the AI engine      |
      | change the OCR settings |

  Scenario Outline: The one where the admin secret is wrong
    When I try to <action> with the wrong admin secret
    Then I am refused

    Examples:
      | action                  |
      | start the AI engine     |
      | stop the AI engine      |
      | change the OCR settings |

  Scenario Outline: The one where the admin secret is correct
    When I <action> with the correct admin secret
    Then the action is accepted

    Examples:
      | action                  |
      | start the AI engine     |
      | stop the AI engine      |
      | change the OCR settings |

  Scenario: The one where a refused request changes nothing
    Given the AI engine is stopped
    When I try to start the AI engine with the wrong admin secret
    Then the AI engine is still stopped
    And the OCR settings are unchanged

  Scenario: The one where anyone can see the engine's state
    Given the AI engine is running
    When I open the OCR settings without the admin secret
    Then I see that the AI engine is running
    And I cannot change anything
