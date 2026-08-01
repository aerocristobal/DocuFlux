@reliability @p1
Feature: Reporting whether the service is healthy enough to take work
  In order to let an orchestrator restart or drain the service correctly
  As a self-hoster running DocuFlux under Docker or Kubernetes
  I want liveness, readiness and detail to mean different things

  Scenario: Liveness answers without touching dependencies
    # A liveness probe that consults Redis restarts the process when Redis blips.
    When I probe liveness
    Then the service reports it is alive

  Scenario: Readiness fails while Redis is unreachable
    Given Redis is unreachable
    When I probe readiness
    Then the service reports it is not ready

  Scenario: Readiness passes when Redis answers
    Given Redis is reachable
    When I probe readiness
    Then the service reports it is ready

  Scenario: Detailed health is unhealthy only when a dependency is down
    Given Redis is unreachable
    When I ask for detailed health
    Then the detailed health is "unhealthy"
    And the detailed health responds with 503

  Scenario Outline: Disk pressure is reported without taking the service down
    Given the data disk is <percent> percent full
    When I ask for detailed health
    Then the disk component is "<disk_status>"
    And the detailed health responds with 200

    Examples:
      | percent | disk_status |
      | 50      | ok          |
      | 92      | warning     |
      | 97      | critical    |

  Scenario: Having no workers degrades health without failing it
    # There is nothing to restart on the web tier; the workers are the problem.
    Given no Celery workers are registered
    When I ask for detailed health
    Then the detailed health is "degraded"
    And the detailed health responds with 200
