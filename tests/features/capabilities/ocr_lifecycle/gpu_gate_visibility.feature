@ocr_lifecycle @p2
Feature: Showing AI engine controls only where they can work
  In order to avoid offering controls that cannot work on this machine
  As a self-hoster
  I want the AI engine controls to appear only with a compatible NVIDIA GPU

  Scenario: The one where a large enough NVIDIA GPU is present
    Given an NVIDIA GPU with 24 GB of memory is available
    When I open the OCR settings
    Then the AI engine controls are shown

  Scenario: The one where the GPU is exactly the minimum size
    Given an NVIDIA GPU with 16 GB of memory is available
    When I open the OCR settings
    Then the AI engine controls are shown

  Scenario: The one where the GPU is too small
    Given an NVIDIA GPU with 8 GB of memory is available
    When I open the OCR settings
    Then the AI engine controls are hidden
    And I am told a GPU with at least 16 GB is needed

  Scenario: The one where there is no GPU
    Given no GPU is available
    When I open the OCR settings
    Then the AI engine controls are hidden

  Scenario: The one where hidden controls cannot be used directly
    Given no GPU is available
    And I am signed in as an admin
    When I try to start the AI engine
    Then the request is refused
    And the reason says no compatible GPU was found
