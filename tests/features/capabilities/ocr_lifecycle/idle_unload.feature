@ocr_lifecycle @p1 @pending
Feature: Freeing the GPU when the AI engine goes unused
  In order to give the GPU back when nobody is using the AI engine
  As a self-hoster who chose on-demand OCR
  I want an engine that started itself to stop after a quiet period

  Background:
    Given OCR mode is set to "on demand"

  Scenario: The one where the default quiet period is 15 minutes
    When I open the OCR settings
    Then the idle timeout is 15 minutes

  Scenario: The one where an idle engine that started itself is stopped
    Given the AI engine was started by a conversion
    And nothing has used it for 15 minutes
    And no jobs are in progress
    And the GPU queue is empty
    When the idle check runs
    Then the AI engine is stopped

  Scenario: The one where a recent conversion keeps the engine alive
    Given the AI engine was started by a conversion
    And a conversion finished 5 minutes ago
    When the idle check runs
    Then the AI engine is still running

  Scenario: The one where a job in progress blocks the unload
    Given the AI engine was started by a conversion
    And nothing has used it for 15 minutes
    And a job is still being converted
    When the idle check runs
    Then the AI engine is still running

  Scenario: The one where waiting GPU jobs block the unload
    Given the AI engine was started by a conversion
    And nothing has used it for 15 minutes
    And a job is waiting in the GPU queue
    When the idle check runs
    Then the AI engine is still running

  Scenario: The one where an admin changes the quiet period
    Given I am signed in as an admin
    When I set the idle timeout to 30 minutes
    Then the idle timeout is 30 minutes
    And an engine idle for 20 minutes is not stopped

  Scenario: The one where a zero timeout is rejected
    Given I am signed in as an admin
    When I set the idle timeout to 0 minutes
    Then the setting is refused
    And the idle timeout is unchanged
