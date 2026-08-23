# Marker v1 → v2 benchmark (do-wqr.4)

marker-pdf **1.10.2** vs **2.0.0**, 10 documents / 213 pages, RTX 3090 (24 GB),
`VLLM_GPU_MEMORY_UTILIZATION=0.75`, `VLLM_GPU_TYPE=3090`.

## Recommendation: PROCEED WITH CAVEATS

v2 is faster and extracts comparably or slightly better. No category lost
extraction quality: every document scored the same, CJK recovery held at 6/6,
and `tables_unrepairable` stayed at 0 throughout.

Three things the runner flags as regressions are explained below and are not
quality losses: the `born_digital/attention` slowdown is vLLM cold start, and
both table-count swings are v2 segmenting tables differently. One is a real
defect and blocks an unguarded rollout: **v2 degenerated once into a repetition
loop**, producing 14× its normal output. That must be bounded before
production traffic reaches it.

## Headline results

| | v1 | v2 | |
|---|---:|---:|---|
| Conversions | 10/10 | 10/10 | no failures either side |
| Model load | 72.2 s | **0.8 s** | 88× — v2 builds thin clients, weights live in the server |
| Aggregate throughput | 0.286 pg/s | 0.351 pg/s | +23% as measured |
| Throughput, warmup-corrected | 0.286 pg/s | **~0.494 pg/s** | +73% (see Warmup) |
| CJK strings recovered | 6/6 | 6/6 | no regression |

## Per-document

The full per-document table is committed as `report-raw.txt`. Extraction volume is within ±6.5% on
9 of 10 documents. Both scanned documents — rasterised to remove the text layer
entirely — improved on v2 (+6.5%, +3.3% chars) *and* ran faster, so the OCR path
is a genuine win rather than a wash.

## The degeneration event

`table_heavy/imagenet` produced **3,217,832 characters (74,833 per page)** during
the batch run — against ~225,000 normally. A dense page holds 3–5k characters, so
this is a repetition loop, not extraction. Table detection collapsed with it
(15 → 5).

It did not reproduce in isolation. Four subsequent runs of the same document,
same version, same config:

    223,322 / 225,730 / 225,554 / 225,690 chars     (0.1% variance)

So: **1 event in 5 runs**, and the affected run was the *tenth document of a
sequential batch* while every clean run was isolated. That points at state
accumulating in the shared vLLM server rather than anything about this PDF.
The sample is far too small to quote a rate; what is established is that it
happens, it is silent, and the quality scorer did not catch it.

**Before rollout:** bound output per page (a document yielding >20k chars/page
is not a document), and decide whether long-lived workers should recycle their
inference server periodically.

## Warmup

`born_digital/attention` took 206.8 s in the batch and appeared as a 4× slowdown.
It is vLLM cold start: the same document alone runs 183.0 s first, **31.2 s
second** — against v1's 46.7 s, i.e. v2 is 1.5× faster in steady state. The
aggregate above carries ~176 s of one-time warmup; the corrected figure removes it.
Any per-document timing that includes the first conversion after server start is
not comparable.

## Table counts moved a lot

`table_heavy/gpt3`: 37 → 71 tables. `imagenet`: 15 → 5 in the degenerate run,
12 in a clean one. v2 reconstructs tables from the pdftext layer for digital
pages and from full-page OCR for scanned ones, with no dedicated table model, so
the segmentation genuinely differs. `tables_unrepairable` stayed at **0
everywhere**, so nothing became malformed — but the counts are not comparing the
same objects, and any threshold keyed to table counts needs re-deriving.

## The quality scorer cannot show improvement

**9 of 10 documents already score 100 on v1.** `shared/quality.py` saturates on
real documents, so it can only detect regression. It flagged nothing here — including
the degeneration event, which it scored 100.

This matters beyond this benchmark: **do-wqr.5 plans to re-tune
`hybrid_quality_threshold` against this metric.** A scorer with no headroom on
good documents is a poor basis for a Pandoc-vs-Marker routing decision. Worth
resolving before that story proceeds.

## Reproducing

    ./benchmarks/marker_v2/run.sh

That is the whole thing: it builds both environments, fetches and derives the
corpus, runs each version, and writes the per-document table to
`$MARKER_BENCH_WORK/results/report-raw.txt`. It is idempotent — re-running skips
environments and documents that already exist. Work lives outside the repo
(default `~/marker-bench`, override with `MARKER_BENCH_WORK`).

To regenerate the comparison from the committed `v1.json` / `v2.json` without
re-running any conversions:

    python3 benchmarks/marker_v2/compare.py

Requires `uv`, a CUDA GPU, docker (marker 2 spawns a vLLM server), and
`pdftoppm`. Python **3.11 is pinned in the runner, not incidental**:
`marker-pdf` pins `pillow<11`, which has no cp314 wheels, so 3.14 fails to
build Pillow from source. `VLLM_GPU_MEMORY_UTILIZATION` defaults to 0.75 rather
than surya's 0.85: on a 24 GB card with a desktop session, 0.85 leaves under
1 GB of headroom.
