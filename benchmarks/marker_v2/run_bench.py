"""Run one marker version over the corpus and emit per-document JSON.

Invoked once per environment:
    envs/v1/bin/python harness/run_bench.py v1
    envs/v2/bin/python harness/run_bench.py v2

Scoring deliberately reuses DocuFlux's own shared/quality.py and
shared/table_postprocess.py rather than a bespoke metric, so the numbers are
the same ones production grades conversions with.
"""
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
# Where corpus/, results/ and envs/ live. Kept outside the repo by default:
# the corpus is ~28MB of third-party PDFs and the venvs are multi-GB.
WORK = os.environ.get("MARKER_BENCH_WORK", os.path.expanduser("~/marker-bench"))
import glob, json, os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = WORK
REPO = _REPO
sys.path.insert(0, os.path.join(REPO, "shared"))
sys.path.insert(0, HERE)

from quality import score_markdown                  # noqa: E402
from table_postprocess import normalize_tables      # noqa: E402
from corpus import CJK_EXPECTED                     # noqa: E402


def page_count(path):
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(path)
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 0


def convert(path, artifacts):
    from marker.converters.pdf import PdfConverter
    from marker.output import text_from_rendered
    conv = PdfConverter(artifact_dict=artifacts, config={})
    rendered = conv(path)
    text, _, images = text_from_rendered(rendered)
    return text, len(images or {})


def main(version):
    from marker.models import create_model_dict
    t0 = time.time()
    artifacts = create_model_dict()
    load_s = time.time() - t0

    out = {"version": version, "model_load_s": round(load_s, 2), "docs": []}
    for path in sorted(glob.glob(os.path.join(ROOT, "corpus", "*.pdf"))):
        name = os.path.basename(path)[:-4]
        category, doc_id = name.split("__", 1)
        pages = page_count(path)
        rec = {"category": category, "doc": doc_id, "pages": pages}
        try:
            t = time.time()
            text, n_images = convert(path, artifacts)
            elapsed = time.time() - t

            report = score_markdown(text, page_count=pages or 1)
            tbl = normalize_tables(text)
            rec.update({
                "ok": True,
                "seconds": round(elapsed, 2),
                "pages_per_sec": round(pages / elapsed, 3) if pages and elapsed else None,
                "chars": len(text),
                "images": n_images,
                "score": report.score,
                "grade": report.grade,
                "reasons": list(report.reason_codes),
                "tables_found": tbl.tables_found,
                "tables_repaired": tbl.tables_repaired,
                "tables_unrepairable": tbl.tables_unrepairable,
            })
            if category == "cjk":
                rec["cjk_found"] = [s for s in CJK_EXPECTED if s in text]
                rec["cjk_missing"] = [s for s in CJK_EXPECTED if s not in text]
        except Exception as e:
            rec.update({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "traceback": traceback.format_exc()[-1200:]})
        out["docs"].append(rec)
        status = "ok" if rec.get("ok") else "FAIL"
        print(f"  {category:14s} {doc_id:10s} {status:4s} "
              f"{rec.get('seconds','-')}s score={rec.get('score','-')}", flush=True)

    dest = os.path.join(ROOT, "results", f"{version}.json")
    with open(dest, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {dest}")


if __name__ == "__main__":
    main(sys.argv[1])
