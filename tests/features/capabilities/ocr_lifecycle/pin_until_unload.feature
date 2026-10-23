@ocr_lifecycle @p1 @pending
Feature: Keeping the AI engine loaded until I unload it
  In order to keep the AI engine warm for a long working session
  As an admin who started the AI engine by hand
  I want it to stay loaded until I stop it myself

  Scenario: The one where a manual start is never auto-stopped
    Given I am signed in as an admin
    And I started the AI engine by hand
    And nothing has used it for much longer than the idle timeout
    And no jobs are in progress
    When the idle check runs
    Then the AI engine is still running

  Scenario: The one where a manual unload releases it
    Given I started the AI engine by hand
    When I stop the AI engine
    Then the AI engine is stopped
    And it is no longer kept loaded

  Scenario: The one where a manual start takes over from an on-demand start
    Given the AI engine was started by a conversion
    When I start the AI engine by hand
    And nothing has used it for much longer than the idle timeout
    And the idle check runs
    Then the AI engine is still running

  Scenario: The one where conversions after a manual stop start it on demand again
    Given I started the AI engine by hand
    And I then stopped it
    And OCR mode is set to "on demand"
    When I ask to convert a PDF using "pdf_marker"
    Then the AI engine begins starting
    And it is eligible for idle unload
