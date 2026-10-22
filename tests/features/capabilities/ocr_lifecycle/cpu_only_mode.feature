@ocr_lifecycle @p1 @pending
Feature: Running without the AI engine
  In order to convert documents on machines with no suitable GPU
  As a self-hoster who chose CPU-only OCR
  I want the AI formats to be unavailable and Tesseract to do the OCR

  Background:
    Given OCR mode is set to "cpu only"

  Scenario Outline: The one where an AI format is not offered
    When I list the available conversion formats
    Then "<format>" is not offered

    Examples:
      | format         |
      | pdf_marker     |
      | pdf_hybrid     |
      | pdf_marker_slm |

  Scenario Outline: The one where an AI format is refused
    When I ask to convert a PDF using "<format>"
    Then the request is refused
    And the reason says the AI engine is disabled

    Examples:
      | format         |
      | pdf_marker     |
      | pdf_hybrid     |
      | pdf_marker_slm |

  Scenario: The one where Tesseract OCR still works
    When I ask to convert a scanned PDF using "pdf_ocr"
    Then the conversion is accepted
    And the text is recognised by Tesseract
    And the AI engine is never started

  Scenario: The one where the AI engine stays off
    Then the AI engine is stopped
    And no GPU memory is used by DocuFlux
