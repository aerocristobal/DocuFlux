"""Produce the v1-vs-v2 delta report.

Note on metrics: DocuFlux's quality scorer saturates at 100 for nearly all
real documents, so it is reported but cannot demonstrate improvement — only
regression. The load-bearing comparisons are extraction volume, table
integrity, throughput, and the CJK assertions.
"""
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
# Where corpus/, results/ and envs/ live. Kept outside the repo by default:
# the corpus is ~28MB of third-party PDFs and the venvs are multi-GB.
WORK = os.environ.get("MARKER_BENCH_WORK", os.path.expanduser("~/marker-bench"))
import json, os, sys

ROOT = os.path.join(WORK, "results")


def load(v):
    with open(os.path.join(ROOT, f"{v}.json")) as f:
        return json.load(f)


def key(r):
    return (r["category"], r["doc"])


def main():
    a, b = load("v1"), load("v2")
    m1 = {key(r): r for r in a["docs"]}
    m2 = {key(r): r for r in b["docs"]}

    print(f"model load: v1 {a['model_load_s']}s  v2 {b['model_load_s']}s\n")
    hdr = f"{'category':14s} {'doc':10s} {'score':>11s} {'chars':>15s} {'tables':>11s} {'sec':>13s} {'pg/s':>13s}"
    print(hdr)
    print("-" * len(hdr))

    regressions, failures = [], []
    for k in sorted(m1):
        r1, r2 = m1[k], m2.get(k, {})
        if not r1.get("ok") or not r2.get("ok"):
            failures.append((k, r1.get("error"), r2.get("error")))
            print(f"{k[0]:14s} {k[1]:10s} {'FAILED':>11s}"
                  f"  v1={'ok' if r1.get('ok') else 'FAIL'} v2={'ok' if r2.get('ok') else 'FAIL'}")
            continue

        ch1, ch2 = r1["chars"], r2["chars"]
        chd = (ch2 - ch1) / ch1 * 100 if ch1 else 0
        s1, s2 = r1["score"], r2["score"]
        t1, t2 = r1["tables_found"], r2["tables_found"]
        u1, u2 = r1["tables_unrepairable"], r2["tables_unrepairable"]
        sec1, sec2 = r1["seconds"], r2["seconds"]
        p1, p2 = r1.get("pages_per_sec"), r2.get("pages_per_sec")

        print(f"{k[0]:14s} {k[1]:10s} {s1:4d}->{s2:<4d} {ch1:7d}->{ch2:<7d}"
              f"({chd:+5.1f}%) {t1:3d}->{t2:<3d} {sec1:6.1f}->{sec2:<6.1f}"
              f" {str(p1):>6s}->{str(p2):<6s}")

        if s2 < s1:
            regressions.append((k, "quality", f"{s1} -> {s2}"))
        if u2 > u1:
            regressions.append((k, "unrepairable tables", f"{u1} -> {u2}"))
        if ch1 and chd < -10:
            regressions.append((k, "extraction volume", f"{chd:+.1f}%"))
        # A large INCREASE is not a win: marker 2 can degenerate into a
        # repetition loop that inflates output. Observed once on imagenet
        # (3.2M chars, 74k/page) at the end of a 10-document batch; isolated
        # re-runs produced 225k consistently. Flag it rather than average it.
        if ch1 and chd > 50:
            regressions.append((k, "output inflation (possible degeneration)",
                                f"{chd:+.1f}%, {ch2/max(r2['pages'],1):,.0f} chars/page"))
        # Tables should not swing wildly in either direction.
        if t1 and (t2 > t1 * 1.5 or t2 < t1 * 0.5):
            regressions.append((k, "table count swing", f"{t1} -> {t2}"))
        # Per-document slowdowns get hidden by the aggregate.
        if sec1 and sec2 > sec1 * 1.5:
            regressions.append((k, "slower", f"{sec1:.0f}s -> {sec2:.0f}s"))

    print()
    for v, d in (("v1", a), ("v2", b)):
        oks = [r for r in d["docs"] if r.get("ok")]
        tot_p = sum(r["pages"] for r in oks)
        tot_s = sum(r["seconds"] for r in oks)
        print(f"{v}: {len(oks)}/{len(d['docs'])} converted, "
              f"{tot_p} pages in {tot_s:.0f}s = {tot_p/tot_s:.3f} pg/s aggregate")

    for v, d in (("v1", a), ("v2", b)):
        cjk = next((r for r in d["docs"] if r["category"] == "cjk" and r.get("ok")), None)
        if cjk:
            print(f"{v} CJK: {len(cjk.get('cjk_found', []))}/"
                  f"{len(cjk.get('cjk_found', [])) + len(cjk.get('cjk_missing', []))} "
                  f"expected strings found; missing={cjk.get('cjk_missing')}")

    print("\nREGRESSIONS" if regressions else "\nNo regressions detected.")
    for k, what, detail in regressions:
        print(f"  {k[0]}/{k[1]}: {what} {detail}")
    for k, e1, e2 in failures:
        print(f"  {k[0]}/{k[1]}: conversion failed  v1={e1} v2={e2}")


if __name__ == "__main__":
    main()
