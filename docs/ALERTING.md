# DocuFlux Alerting Guide

This document describes the 15 alerting rules configured for DocuFlux in `deploy/monitoring/alerts.yml` and provides runbook guidance for operators.

**Queue note:** the metrics exporter publishes `docuflux_queue_depth` for the `celery`, `default`, `high_priority` and `gpu` queues, but `QueueBacklog` and `QueueStalled` only match `queue_name="celery"`. DocuFlux routes work to `default`, `high_priority` and `gpu`, so these two rules do not watch the queues actually in use until the rule expressions are widened.

## Alert Severity Levels

| Severity | Priority | Response Time | Description |
|----------|----------|---------------|-------------|
| **critical** | P0 | Immediate | Service down or critical failure requiring immediate attention |
| **warning** | P1 | < 1 hour | Degraded performance or approaching critical threshold |
| **info** | P2 | Best effort | Informational alerts for trend tracking |

## Configured Alerts

### Service Health Alerts

#### DocuFluxServiceDown
**Severity**: Critical
**Trigger**: Web or worker service down for >1 minute

**Symptoms**:
- Container stopped or crashed
- Health check failures
- Service unavailable errors

**Investigation Steps**:
1. Check container status: `docker ps -a | grep docuflux`
2. View logs: `docker-compose logs web worker`
3. Check resource limits: `docker stats`
4. Restart if needed: `docker-compose restart web worker`

**Common Causes**:
- Out of memory (OOM) kill
- Uncaught exception in application code
- Resource limit exceeded
- Dependency failure (Redis down)

---

#### RedisConnectionFailed
**Severity**: Critical
**Trigger**: Redis unreachable for >30 seconds

**Symptoms**:
- All conversions fail
- Job status not updating
- WebSocket connections fail

**Investigation Steps**:
1. Check Redis container: `docker-compose logs redis`
2. Test connection: `docker exec docuflux-redis-1 redis-cli ping`
3. Check memory usage: `docker stats docuflux-redis-1`
4. Restart if needed: `docker-compose restart redis`

**Common Causes**:
- Redis maxmemory limit reached
- Redis container crashed
- Network connectivity issues
- Redis config error

---

### Task Performance Alerts

#### HighTaskFailureRate
**Severity**: Warning
**Trigger**: >10% of tasks failing over 5 minutes

**Symptoms**:
- Users reporting conversion failures
- Increased error logs
- Specific format combinations failing

**Investigation Steps**:
1. Check worker logs: `docker-compose logs worker | grep ERROR`
2. Query metrics: `curl http://localhost:9090/metrics | grep conversion_failures`
3. Identify failing formats: Check `format_from` and `format_to` labels
4. Test problematic conversion manually

**Common Causes**:
- Pandoc errors with specific format combinations
- Marker AI out of GPU memory
- Corrupt input files
- Missing dependencies

---

#### SlowConversionTasks
**Severity**: Warning
**Trigger**: p95 conversion duration > 300 s for >10 minutes

**Investigation Steps**:
1. Check which formats are slow (`docuflux_conversion_duration_seconds` by format labels).
2. A Marker job after an idle period includes the Surya cold start (about 3 minutes); check `GET /api/v1/admin/surya` for `state` and consider pinning with a manual load.
3. Check queue depth and `surya-vlm` health.

---

#### CriticalTaskFailureRate
**Severity**: Critical
**Trigger**: >50% of tasks failing over 2 minutes

**Symptoms**:
- Service effectively unusable
- Mass conversion failures
- Worker repeatedly crashing

**Investigation Steps**:
1. **IMMEDIATE**: Check worker health: `curl http://localhost:9090/healthz`
2. View recent failures: `docker-compose logs worker --tail=100 | grep FAILURE`
3. Check GPU status (if applicable): `docker exec docuflux-worker-1 nvidia-smi`
4. Consider rolling back recent changes

**Common Causes**:
- Worker misconfiguration
- GPU driver failure
- Celery worker deadlock
- Critical dependency missing

---

### Resource Usage Alerts

#### HighMemoryUsage
**Severity**: Warning
**Trigger**: Worker container memory > 90% of its limit for >5 minutes (cAdvisor metrics)

**Investigation Steps**:
1. Check usage: `docker stats`
2. Check for oversized inputs or a stuck task; workers recycle every 50 tasks (`max_tasks_per_child`).
3. Raise the worker memory limit if usage is legitimate.

---

#### DiskSpaceWarning
**Severity**: Warning
**Trigger**: Disk usage >80% for >5 minutes

**Symptoms**:
- Slow file operations
- Cleanup task running frequently
- Users reporting slow conversions

**Investigation Steps**:
1. Check disk usage: `df -h /app/data`
2. List large directories: `du -sh /app/data/* | sort -h`
3. Check cleanup logs: `docker-compose logs worker | grep cleanup`
4. Manual cleanup if needed: Trigger cleanup task or delete old jobs

**Common Causes**:
- Cleanup task not running
- Large batch of conversions
- Retention policy too generous
- Disk quota reached

---

#### DiskSpaceCritical
**Severity**: Critical (P0)
**Trigger**: Disk usage >95% for >1 minute

**Symptoms**:
- Uploads fail
- Conversions fail with write errors
- Emergency cleanup triggered
- Service degraded or down

**Investigation Steps**:
1. **IMMEDIATE**: Free space by deleting old conversions
2. Check emergency cleanup logs: `docker-compose logs worker | grep EMERGENCY`
3. Identify largest files: `find /app/data -type f -exec du -h {} + | sort -rh | head -20`
4. Delete manually if needed: `rm -rf /app/data/outputs/<old-job-ids>`

**Common Causes**:
- Cleanup failed or disabled
- Unusually large file uploads
- Disk quota too small for workload
- Retention policy misconfigured

---

### GPU Alerts

#### GPUUnavailable
**Severity**: Warning
**Trigger**: GPU utilization 0% for >5 minutes

**Symptoms**:
- Marker conversions disabled or slow
- GPU status shows "unavailable"
- No GPU metrics

**Investigation Steps**:
1. Check GPU visibility on the VLM server (marker 2 owns the GPU there, not the worker): `docker exec docuflux-surya-vlm-1 nvidia-smi`
2. Check the inference server health: `curl http://localhost:8000/health`
3. Check worker attach env: `docker inspect docuflux-worker-1 | grep -A6 SURYA_INFERENCE`
4. Check CUDA on the worker: `docker exec docuflux-worker-1 python3 -c "import torch; print(torch.cuda.is_available())"`
5. Restart the VLM server first, then the worker: `docker-compose restart surya-vlm worker`

**Common Causes**:
- GPU driver crash
- NVIDIA Container Toolkit misconfigured
- GPU reserved by another process
- GPU power management issue

---

#### GPUMemoryHigh
**Severity**: Warning
**Trigger**: GPU memory used / total > 95% for >2 minutes

**Investigation Steps**:
1. Check VRAM on the inference server: `docker exec docuflux-surya-vlm-1 nvidia-smi`
2. Note vLLM pre-allocates a fixed share (`VLLM_GPU_MEMORY_UTILIZATION`, 0.85 in the base compose file), so sustained high usage is partly expected; look for other processes on the GPU.
3. If jobs fail with CUDA out-of-memory, lower `VLLM_GPU_MEMORY_UTILIZATION` or restart `surya-vlm`.

---

#### GPUTemperatureHigh
**Severity**: Warning
**Trigger**: GPU temperature > 85 C for >5 minutes

**Investigation Steps**:
1. Check temperature and fan: `nvidia-smi --query-gpu=temperature.gpu,fan.speed --format=csv`
2. Check airflow and workload; consider limiting concurrent Marker work.

---

### Queue Health Alerts

#### QueueBacklog
**Severity**: Warning
**Trigger**: >100 tasks in queue for >10 minutes

**Symptoms**:
- Slow conversion times
- Jobs stuck in PENDING state
- High queue depth metric

**Investigation Steps**:
1. Check queue depth: `curl http://localhost:9090/metrics | grep queue_depth`
2. Check worker capacity: `docker stats docuflux-worker-1`
3. Check active tasks: `celery -A tasks inspect active`
4. Consider scaling workers if persistent

**Common Causes**:
- Worker overloaded
- Slow conversions (large files)
- Insufficient worker resources
- Marker AI slow on CPU

---

#### QueueStalled
**Severity**: Critical
**Trigger**: Queue depth unchanged for 15 minutes with >10 jobs

**Symptoms**:
- Jobs not processing
- Worker appears hung
- No task completion

**Investigation Steps**:
1. **IMMEDIATE**: Check worker status: `docker-compose ps worker`
2. Check for deadlock: `docker exec docuflux-worker-1 ps aux`
3. Check worker logs: `docker-compose logs worker --tail=50`
4. Restart worker: `docker-compose restart worker`

**Common Causes**:
- Worker deadlock
- Celery pool crash
- Task timeout without cleanup
- Redis connection lost

---

### Worker Health Alerts

#### NoActiveWorkers
**Severity**: Critical
**Trigger**: `docuflux_worker_tasks_active` is 0 with no change for 10 minutes (fires after 5 minutes)

This also fires on an idle system with no traffic; correlate with queue depth before treating it as an outage.

**Investigation Steps**:
1. Check workers: `docker-compose ps worker worker-light`
2. Check logs: `docker-compose logs worker worker-light --tail=50`
3. Restart if unhealthy: `docker-compose restart worker worker-light`

---

#### WorkerTasksStuck
**Severity**: Warning
**Trigger**: `docuflux_worker_tasks_active` > 0 with no change for 30 minutes (fires after 15 minutes)

**Investigation Steps**:
1. List active tasks: `docker-compose exec worker celery -A tasks inspect active`
2. Marker tasks are limited to 20 minutes (hard limit 1200 s); a Marker job waiting on a Surya cold start is bounded by `SURYA_COLD_START_TIMEOUT_SECONDS`. Check `surya-vlm` and `surya-controller` logs.
3. Restart the stuck worker if the task is past its limit.

---

## Alert Configuration

### Prometheus Configuration

Add to `prometheus.yml`:

```yaml
rule_files:
  - "/etc/prometheus/alerts.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - "alertmanager:9093"
```

### Alertmanager Configuration

Example `alertmanager.yml`:

```yaml
global:
  resolve_timeout: 5m
  slack_api_url: 'YOUR_SLACK_WEBHOOK_URL'

route:
  group_by: ['alertname', 'component']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 12h
  receiver: 'slack-critical'
  routes:
    - match:
        severity: critical
      receiver: 'slack-critical'
    - match:
        severity: warning
      receiver: 'slack-warnings'

receivers:
  - name: 'slack-critical'
    slack_configs:
      - channel: '#docuflux-alerts-critical'
        title: '🚨 CRITICAL: {{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'

  - name: 'slack-warnings'
    slack_configs:
      - channel: '#docuflux-alerts'
        title: '⚠️ WARNING: {{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'
```

## Testing Alerts

### Trigger Test Alerts

```bash
# Test disk space alert
dd if=/dev/zero of=/app/data/testfile bs=1M count=10000

# Test high failure rate (submit invalid conversions)
for i in {1..20}; do
  curl -F "file=@invalid.txt" -F "from=invalid" -F "to=pdf" http://localhost:5000/convert
done

# Test queue backlog
for i in {1..150}; do
  curl -F "file=@large.pdf" -F "from=pdf_marker" -F "to=markdown" http://localhost:5000/convert
done

# Cleanup
rm /app/data/testfile
```

### Verify Alert Rules

```bash
# Check alert rules syntax
promtool check rules deploy/monitoring/alerts.yml

# Test alert expression
promtool query instant http://localhost:9090 \
  '(docuflux_disk_usage_bytes / docuflux_disk_total_bytes) > 0.80'
```

## Monitoring Dashboards

Recommended Grafana dashboards for DocuFlux:

1. **Service Health Dashboard**
   - Service uptime
   - Request rate
   - Error rate
   - Response time

2. **Resource Usage Dashboard**
   - CPU usage
   - Memory usage
   - Disk usage
   - GPU utilization

3. **Task Performance Dashboard**
   - Conversion rate
   - Success/failure ratio
   - Task duration histogram
   - Queue depth

## On-Call Playbook

### Critical Alert Response (P0)

1. **Acknowledge** alert in Alertmanager/PagerDuty
2. **Assess** impact:
   - How many users affected?
   - Is service completely down?
   - Is data at risk?
3. **Mitigate** immediately:
   - Restart affected services
   - Scale resources if needed
   - Enable maintenance mode if necessary
4. **Investigate** root cause
5. **Resolve** and document
6. **Post-mortem** within 24 hours

### Warning Alert Response (P1)

1. **Review** alert details
2. **Investigate** within 1 hour
3. **Plan** resolution if needed
4. **Monitor** for escalation to critical
5. **Document** findings

## Related Documentation

- [Prometheus Metrics](../worker/metrics.py) - Metrics definitions
- [Health Checks](../web/app.py) - Health endpoint implementation
- [Cleanup Logic](../worker/tasks/maintenance.py) - Data retention and cleanup (`worker/tasks/` package)
- [GPU Monitoring](../worker/warmup.py) - GPU detection and monitoring

## Support

For issues with alerting:
- Check Prometheus targets: http://localhost:9090/targets
- View active alerts: http://localhost:9090/alerts
- Check Alertmanager: http://localhost:9093
- Review logs: `docker-compose logs prometheus alertmanager`
