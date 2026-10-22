@ocr_lifecycle @p1 @pending
Feature: Turning the AI engine on and off by hand
  In order to have the AI engine ready when I need it and free the GPU when I do not
  As a self-hoster with a compatible NVIDIA GPU
  I want to start and stop the AI engine from the settings page

  Scenario: The one where an admin starts the AI engine
    Given the AI engine is stopped
    And I am signed in as an admin
    When I start the AI engine
    Then the AI engine is reported as starting
    And it later becomes ready for conversions

  Scenario: The one where an admin stops the AI engine
    Given the AI engine is running
    And I am signed in as an admin
    When I stop the AI engine
    Then the AI engine is reported as stopped
    And the GPU memory it held is released

  Scenario: The one where the settings page shows the engine's current state
    Given the AI engine is running
    When I open the OCR settings
    Then I see that the AI engine is running
