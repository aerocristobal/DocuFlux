"""Build the two locally-derived corpus documents.

Run with the v2 venv python (needs Pillow + weasyprint).
"""
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
# Where corpus/, results/ and envs/ live. Kept outside the repo by default:
# the corpus is ~28MB of third-party PDFs and the venvs are multi-GB.
WORK = os.environ.get("MARKER_BENCH_WORK", os.path.expanduser("~/marker-bench"))
import os, subprocess, sys, glob
sys.path.insert(0, os.path.dirname(__file__))
from corpus import CORPUS, SCANNED_FROM, CJK_HTML

ROOT = os.path.join(WORK, "corpus")


def rasterise(src_id):
    """Strip the text layer: render to JPEG at 150 DPI, re-wrap via Pillow."""
    src = next(os.path.join(ROOT, f"{c}__{d}.pdf") for c, d, _ in CORPUS if d == src_id)
    out = os.path.join(ROOT, f"scanned__{src_id}.pdf")
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        return out, "cached"
    tmp = f"/tmp/rast_{src_id}"
    subprocess.run(["pdftoppm", "-r", "150", "-jpeg", src, tmp], check=True)
    pages = sorted(glob.glob(f"{tmp}-*.jpg"))
    if not pages:
        raise RuntimeError("pdftoppm produced no pages")
    from PIL import Image
    imgs = [Image.open(p).convert("RGB") for p in pages]
    imgs[0].save(out, save_all=True, append_images=imgs[1:])
    for p in pages:
        os.remove(p)
    return out, f"{os.path.getsize(out)//1024}KB, {len(pages)}p"


def build_cjk():
    out = os.path.join(ROOT, "cjk__generated.pdf")
    if os.path.exists(out) and os.path.getsize(out) > 3000:
        return out, "cached"
    from weasyprint import HTML
    HTML(string=CJK_HTML).write_pdf(out)
    return out, f"{os.path.getsize(out)//1024}KB"


if __name__ == "__main__":
    for sid in SCANNED_FROM:
        try:
            _, note = rasterise(sid)
            print(f"  scanned__{sid:12s} {note}")
        except Exception as e:
            print(f"  scanned__{sid:12s} FAILED: {e}")
    try:
        _, note = build_cjk()
        print(f"  cjk__generated       {note}")
    except Exception as e:
        print(f"  cjk__generated       FAILED: {e}")
