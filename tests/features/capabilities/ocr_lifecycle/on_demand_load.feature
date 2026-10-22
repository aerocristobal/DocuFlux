@ocr_lifecycle @p1 @pending
Feature: Starting the AI engine when a conversion needs it
  In order to avoid holding GPU memory while nobody is converting
  As a self-hoster who chose on-demand OCR
  I want the AI engine to start itself when a job needs it

  Background:
    Given OCR mode is set to "on demand"

  Scenario: The one where a conversion starts a stopped engine
    Given the AI engine is stopped
    When I ask to convert a PDF using "pdf_marker"
    Then the request is accepted
    And the AI engine begins starting

  Scenario: The one where the job waits with visible progress
    Given the AI engine is stopped
    When I ask to convert a PDF using "pdf_marker"
    Then my job shows the progress message "Starting AI engine"
    And my job is not marked as failed while the engine loads

  Scenario: The one where the job runs once the engine is ready
    Given the AI engine is stopped
    And I have asked to convert a PDF using "pdf_marker"
    When the AI engine becomes ready
    Then my job proceeds to convert the document
    And the conversion completes

  Scenario: The one where a running engine is reused
    Given the AI engine is running
    When I ask to convert a PDF using "pdf_marker"
    Then my job starts without any engine start-up wait

  Scenario: The one where the engine never becomes ready
    Given the AI engine is stopped
    And the AI engine cannot start
    When I ask to convert a PDF using "pdf_marker"
    Then my job fails after the start-up time limit
    And the reason says the AI engine did not start

  Scenario: The one where the engine was started by a conversion
    Given the AI engine is stopped
    When I ask to convert a PDF using "pdf_marker"
    Then the AI engine is recorded as started on demand
