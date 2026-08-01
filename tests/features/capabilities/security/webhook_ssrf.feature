@security @p0
Feature: Refusing to make requests to the deployment's own network
  In order to stop DocuFlux being used to reach services only it can see
  As the operator of a deployment inside a private network
  I want webhook destinations validated before anything is sent to them

  Background:
    Given a job exists to attach a webhook to

  Scenario Outline: Addresses inside the deployment's network are refused
    When I register the webhook "<url>"
    Then the webhook is refused as a bad request

    Examples:
      | url                                        |
      | http://localhost:8080/hook                 |
      | http://127.0.0.1/hook                      |
      | http://10.0.0.5/hook                       |
      | http://192.168.1.10/hook                   |
      | http://169.254.169.254/latest/meta-data    |

  Scenario: A non-http scheme is refused
    When I register the webhook "ftp://example.test/hook"
    Then the webhook is refused as a bad request

  Scenario: A public address is accepted
    When I register a webhook on a public address
    Then the webhook is registered

  Scenario: Attaching a webhook to a job that does not exist is refused
    Given no such job
    When I register a webhook on a public address
    Then the job is reported as not found

  Scenario: The destination is checked again at delivery time
    # A hostname that resolved publicly at registration can be repointed at an
    # internal address before the conversion finishes.
    Given a webhook registered for a job
    When the destination now resolves to a private address
    And the job completes
    Then nothing is sent to the destination
