# Supported Formats

The service supports a wide range of document formats using **Pandoc**, **Marker** (for high-fidelity PDF extraction) and **Tesseract** (CPU-only OCR for scanned PDFs). The canonical list is `shared/formats.py`.

## Input Formats
These formats can be uploaded for conversion:

| Format Name | Extension | Category | Key | Notes |
|-------------|-----------|----------|-----|-------|
| Pandoc Markdown | `.md` | Markdown | `markdown` | Standard Pandoc flavor |
| GitHub Flavored Markdown | `.md` | Markdown | `gfm` | |
| HTML5 | `.html` | Web | `html` | |
| Jupyter Notebook | `.ipynb` | Web | `ipynb` | |
| Microsoft Word | `.docx` | Office | `docx` | |
| OpenOffice / LibreOffice | `.odt` | Office | `odt` | |
| Rich Text Format | `.rtf` | Office | `rtf` | |
| EPUB (v3) | `.epub` | E-Books | `epub3` | |
| EPUB (v2) | `.epub` | E-Books | `epub2` | |
| LaTeX | `.tex` | Technical | `latex` | |
| PDF (High Accuracy) | `.pdf` | Technical | `pdf_marker` | **AI-Powered**, input only. Uses deep learning (OCR/Layout) to extract markdown via the `surya-vlm` inference server. Needs an NVIDIA GPU with 16 GB+ VRAM; rejected with HTTP 409 when `OCR_MODE=cpu_only`. |
| PDF (Hybrid) | `.pdf` | Technical | `pdf_hybrid` | Input only. Tries Pandoc first and falls back to Marker when the output quality score is below `HYBRID_QUALITY_THRESHOLD` (default 60). Marker-dependent, so also 409 in `cpu_only`. |
| PDF (AI + SLM Refine) | `.pdf` | Technical | `pdf_marker_slm` | Input only. Marker followed by SLM refinement of OCR artifacts. Marker-dependent, so also 409 in `cpu_only`. |
| PDF (OCR - CPU) | `.pdf` | Technical | `pdf_ocr` | Input only. Tesseract OCR for scanned PDFs on deployments without a suitable GPU; always available (runs on the light lane). |
| AsciiDoc | `.adoc` | Technical | `asciidoc` | |
| reStructuredText | `.rst` | Technical | `rst` | |
| BibTeX | `.bib` | Technical | `bibtex` | Bibliography files |
| MediaWiki | `.wiki` | Wiki | `mediawiki` | |
| Jira Wiki | `.txt` | Wiki | `jira` | |

## Output Formats
These formats can be selected as the target. Formats marked `Both` in `shared/formats.py` can be used as input and output; the `pdf_*` Marker/OCR formats are input only, and the two below are output only:

| Format Name | Extension | Category | Notes |
|-------------|-----------|----------|-------|
| Microsoft PowerPoint | `.pptx` | Office | **Output Only**. Can generate slides from Markdown/HTML. |
| PDF (via LaTeX) | `.pdf` | Technical | **Output Only**. High-quality typesetting via LaTeX engine. |
| *All `Both` formats* | *Various* | *All* | Everything except the input-only PDF formats (`pdf_marker`, `pdf_hybrid`, `pdf_marker_slm`, `pdf_ocr`). You can convert *to* Markdown, HTML, Docx, etc. |

## Compatibility Matrix

### General Rule
Most text-based formats (Markdown, HTML, LaTeX, RST, Wiki) can be converted to any other text-based format or Office format (Docx, ODT, PPTX, PDF).

### PDF Logic
- **Input**:
    - Standard PDF input is **not** supported by Pandoc directly with high quality.
    - Use one of the PDF input engines (`pdf_marker`, `pdf_hybrid`, `pdf_marker_slm`, or `pdf_ocr` on CPU-only hosts), which convert PDF -> Markdown. From there, you can convert that Markdown to any other format in a second step (no chaining yet). Currently these engines' output is **Markdown**.
- **Output**:
    - We generate PDFs using the `xelatex` engine (with CJK/Unicode support). This requires the source document to be convertible to LaTeX first (which Pandoc handles).

### Common Conversions
- **Markdown -> PDF**: Excellent.
- **Word (Docx) -> PDF**: Good.
- **HTML -> EPUB**: Excellent.
- **PDF (Image/Scan) -> Markdown**: Uses `pdf_marker` (high accuracy, needs a GPU; slower on a cold start) or `pdf_ocr` (Tesseract, CPU-only, lower fidelity).
