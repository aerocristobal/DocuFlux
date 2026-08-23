"""Fetch and build the benchmark corpus. Idempotent: skips what exists."""
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
# Where corpus/, results/ and envs/ live. Kept outside the repo by default:
# the corpus is ~28MB of third-party PDFs and the venvs are multi-GB.
WORK = os.environ.get("MARKER_BENCH_WORK", os.path.expanduser("~/marker-bench"))
import os, subprocess, sys, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from corpus import CORPUS, SCANNED_FROM, CJK_HTML

ROOT = os.path.join(WORK, "corpus")


def fetch(category, doc_id, url):
    out = os.path.join(ROOT, f"{category}__{doc_id}.pdf")
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        return out, "cached"
    req = urllib.request.Request(url, headers={"User-Agent": "marker-bench/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
        f.write(r.read())
    return out, f"{os.path.getsize(out)//1024}KB"


def rasterise(src_id):
    """Strip the text layer by rendering to images and re-wrapping as PDF."""
    src = next(os.path.join(ROOT, f"{c}__{d}.pdf") for c, d, _ in CORPUS if d == src_id)
    out = os.path.join(ROOT, f"scanned__{src_id}.pdf")
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        return out, "cached"
    # 150 DPI is a realistic scan resolution and keeps the file manageable.
    subprocess.run(["pdftoppm", "-r", "150", "-jpeg", src, "/tmp/rast"], check=True)
    pages = sorted(p for p in os.listdir("/tmp") if p.startswith("rast-"))
    subprocess.run(["img2pdf", "-o", out] + [f"/tmp/{p}" for p in pages], check=True)
    for p in pages:
        os.remove(f"/tmp/{p}")
    return out, f"{os.path.getsize(out)//1024}KB"


if __name__ == "__main__":
    os.makedirs(ROOT, exist_ok=True)
    for cat, doc, url in CORPUS:
        try:
            path, note = fetch(cat, doc, url)
            print(f"  {cat:14s} {doc:10s} {note}")
        except Exception as e:
            print(f"  {cat:14s} {doc:10s} FAILED: {e}")
    open(os.path.join(ROOT, "cjk__generated.html"), "w").write(CJK_HTML)
    print("  cjk            generated  html written (PDF built by build_cjk.py)")
