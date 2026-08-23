"""Corpus definition for the Marker v1-vs-v2 benchmark.

Sourced documents are fetched from stable public URLs and verified by size
rather than committed, so the repo does not carry tens of MB of PDFs.

Two categories are built locally instead of downloaded:

  scanned  - a born-digital source rasterised to images and re-wrapped as a
             PDF. This gives a controlled pair: identical content and page
             count, one with a text layer and one without, so any delta is
             attributable to the OCR path rather than to document differences.
  cjk      - generated from HTML with a Noto CJK font. Real CJK PDFs with
             predictable licensing are hard to source; generating one makes
             the expected text known exactly, which turns CJK extraction into
             a checkable result rather than a subjective read.
"""

CORPUS = [
    # category, doc id, url
    ("born_digital", "attention", "https://arxiv.org/pdf/1706.03762v7"),
    ("born_digital", "bert",      "https://arxiv.org/pdf/1810.04805v2"),
    ("multi_column", "resnet",    "https://arxiv.org/pdf/1512.03385v1"),
    ("multi_column", "adam",      "https://arxiv.org/pdf/1412.6980v9"),
    ("table_heavy",  "gpt3",      "https://arxiv.org/pdf/2005.14165v4"),
    ("table_heavy",  "imagenet",  "https://arxiv.org/pdf/1409.0575v3"),
    ("equations",    "gan",       "https://arxiv.org/pdf/1406.2661v1"),
]

# Rasterised (text-layer-free) counterparts built from these sources.
SCANNED_FROM = ["attention", "resnet"]

# Known-content CJK document, generated locally.
CJK_HTML = """<html><head><meta charset="utf-8"><style>
body { font-family: "Noto Sans CJK JP", "Noto Sans CJK SC", sans-serif; font-size: 12pt; }
table { border-collapse: collapse; } td, th { border: 1px solid #333; padding: 4px 8px; }
</style></head><body>
<h1>文書変換テスト</h1>
<p>この文書はマーカーのCJK抽出を検証するために生成されました。
日本語、中文、한국어の三つの言語が含まれています。</p>
<h2>表のテスト</h2>
<table>
<tr><th>項目</th><th>数量</th><th>備考</th></tr>
<tr><td>第一項</td><td>百二十三</td><td>テスト</td></tr>
<tr><td>第二項</td><td>四百五十六</td><td>確認</td></tr>
</table>
<h2>中文段落</h2>
<p>这是一段中文文本，用于测试字符提取的准确性。标点符号：、。！？</p>
<h2>한국어 문단</h2>
<p>이것은 한국어 텍스트입니다. 문자 추출을 확인합니다.</p>
</body></html>"""

# Strings that must survive extraction for the CJK doc to count as correct.
CJK_EXPECTED = ["文書変換テスト", "日本語", "中文", "한국어", "百二十三", "标点符号"]
