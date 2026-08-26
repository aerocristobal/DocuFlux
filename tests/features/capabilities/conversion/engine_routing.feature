@conversion @p0
Feature: Routing conversions to the right engine and queue
  In order to get the best engine for each document without paying GPU cost needlessly
  As a self-hoster running both GPU and CPU workers
  I want each submission routed to the correct task and queue

  # This routing is duplicated in three places — _enqueue_convert_job, retry_job and
  # _enqueue_v1_convert_job — and has already drifted once, when convert_with_ocr was
  # added without a queue-routing entry. These scenarios pin all three call sites.

  Background:
    Given the service has free disk space
    And I am browsing with a session

  Scenario Outline: AI engines are dispatched to the GPU queue
    When I submit "scan.pdf" converting <format> to markdown
    Then the task <task> is dispatched to the gpu queue

    Examples:
      | format         | task                          |
      | pdf_marker     | tasks.convert_with_marker     |
      | pdf_hybrid     | tasks.convert_with_hybrid     |
      | pdf_marker_slm | tasks.convert_with_marker_slm |

  Scenario: CPU-based OCR never occupies the GPU queue
    When I submit "scan.pdf" converting pdf_ocr to markdown
    Then the task tasks.convert_with_ocr is dispatched
    And it is not dispatched to the gpu queue

  Scenario: A small document takes the fast lane
    When I submit "notes.md" of 1 MB converting markdown to html
    Then the task is dispatched to the high_priority queue

  Scenario: A large document takes the default lane so it cannot block small ones
    When I submit "notes.md" of 9 MB converting markdown to html
    Then the task is dispatched to the default queue

  Scenario: Retrying a GPU job keeps it on the GPU queue
    Given a completed job converted with pdf_marker
    When I retry that job
    Then the task tasks.convert_with_marker is dispatched to the gpu queue

  Scenario: Retrying a CPU job uses the default queue rather than the fast lane
    Given a completed job converted with markdown
    When I retry that job
    Then the task is dispatched to the default queue

  Scenario: AI engines receive the OCR options
    When I submit "scan.pdf" converting pdf_marker to markdown
    Then the task options include force_ocr

  Scenario: AI engines receive the LLM options when allowlist permits
    When I submit "scan.pdf" converting pdf_marker to markdown
    And use_llm is on the allowlist
    Then the task options include use_llm

  Scenario: Pandoc conversions carry no engine options
    When I submit "notes.md" converting markdown to html
    Then the task is dispatched with no options
