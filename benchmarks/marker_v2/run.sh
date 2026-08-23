#!/usr/bin/env bash
# Marker v1-vs-v2 benchmark. One command, idempotent — re-running skips
# environments and corpus documents that already exist.
#
#   ./benchmarks/marker_v2/run.sh
#
# Requires: uv, a CUDA GPU, docker (marker 2 spawns a vLLM server), pdftoppm.
# Work lives outside the repo; override with MARKER_BENCH_WORK.
set -euo pipefail

WORK="${MARKER_BENCH_WORK:-$HOME/marker-bench}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MARKER_BENCH_WORK="$WORK"

# Python 3.11 is required, not incidental: marker-pdf pins pillow<11, which has
# no wheels for 3.14, and building it from source fails.
PY_VERSION=3.11

# 0.75 rather than surya's 0.85 default: on a 24GB card the desktop session
# holds ~3GB, and 0.85 leaves under 1GB of headroom.
export VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.75}"
export VLLM_GPU_TYPE="${VLLM_GPU_TYPE:-3090}"
export TORCH_DEVICE="${TORCH_DEVICE:-cuda}"

mkdir -p "$WORK"/{envs,corpus,results}

for v in v1 v2; do
  case $v in v1) spec="marker-pdf[full]==1.10.2" ;; v2) spec="marker-pdf[full]==2.0.0" ;; esac
  if [ ! -x "$WORK/envs/$v/bin/python" ]; then
    echo "== building $v env ($spec)"
    uv venv --python "$PY_VERSION" "$WORK/envs/$v"
    uv pip install --python "$WORK/envs/$v/bin/python" "$spec"
  else
    echo "== $v env present"
  fi
done

echo "== corpus"
python3 "$HERE/fetch_corpus.py"
"$WORK/envs/v2/bin/python" "$HERE/build_derived.py"

# Sequential, not parallel: both versions want the whole GPU.
echo "== v1 run"
"$WORK/envs/v1/bin/python" "$HERE/run_bench.py" v1
echo "== v2 run"
"$WORK/envs/v2/bin/python" "$HERE/run_bench.py" v2

echo "== comparison"
python3 "$HERE/compare.py" | tee "$WORK/results/report-raw.txt"
