# Archived documentation

Documents here are kept for history only. Each file carries a DEPRECATED banner at the top; do not follow them as current guidance.

| File | Date archived | Reason | Superseded by |
|---|---|---|---|
| [SECURITY_FIXES.md](SECURITY_FIXES.md) | 2026-10-22 | Historical CodeQL remediation record; file/line references stale | CI Bandit/CodeQL gates (`.github/workflows/ci.yml`); security section of [../ARCHITECTURE.md](../ARCHITECTURE.md) |
| [IMPLEMENTATION_SUMMARY_CJK.md](IMPLEMENTATION_SUMMARY_CJK.md) | 2026-10-22 | Dated implementation record with stale line references | [../FORMATS.md](../FORMATS.md) |
| [CLOUDFLARE_TUNNEL_SETUP.md](CLOUDFLARE_TUNNEL_SETUP.md) | 2026-10-22 | Compose overlay/profile removed in #97; `deploy/cloudflare/setup.sh` still creates the tunnel and writes `.env`, but its `--profile https` next step is stale | `deploy/cloudflare/setup.sh` (tunnel creation only); [../DEPLOYMENT.md](../DEPLOYMENT.md) |
| [SPRINT-BACKLOG.md](SPRINT-BACKLOG.md) | 2026-10-22 | PR #108 follow-ups; PR merged, scope no longer maintained | beads (`bd list --all`); [../user-stories/BACKLOG.md](../user-stories/BACKLOG.md) |
| [marker_v2_REPORT.md](marker_v2_REPORT.md) | 2026-10-22 | One-off v1 vs v2 Marker benchmark snapshot; rollout decision shipped | [../ARCHITECTURE.md](../ARCHITECTURE.md#inference-server-recycling); [../AI_INTEGRATION.md](../AI_INTEGRATION.md); re-run `benchmarks/marker_v2/run.sh` |
| [marker_v2_report-raw.txt](marker_v2_report-raw.txt) | 2026-10-22 | Raw per-document output for the benchmark above | `benchmarks/marker_v2/run.sh` (writes to `$MARKER_BENCH_WORK/results/`) |
