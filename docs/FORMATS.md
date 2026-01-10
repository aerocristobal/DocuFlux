# Supported Formats

The service supports a wide range of document formats using **Pandoc** and **Marker** (for high-fidelity PDF extraction).

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
| PDF (High Accuracy) | `.pdf` | Technical | `pdf_marker` | **AI-Powered**. Uses deep learning (OCR/Layout) to extract markdown. Requires GPU for speed. |
| AsciiDoc | `.adoc` | Technical | `asciidoc` | |
| reStructuredText | `.rst` | Technical | `rst` | |
| BibTeX | `.bib` | Technical | `bibtex` | Bibliography files |
| MediaWiki | `.wiki` | Wiki | `mediawiki` | |
| Jira Wiki | `.txt` | Wiki | `jira` | |

## Output Formats
These formats can be selected as the target:

| Format Name | Extension | Category | Notes |
|-------------|-----------|----------|-------|
| Microsoft PowerPoint | `.pptx` | Office | **Output Only**. Can generate slides from Markdown/HTML. |
| PDF (via LaTeX) | `.pdf` | Technical | **Output Only**. High-quality typesetting via LaTeX engine. |
| *All Input Formats* | *Various* | *All* | Except `pdf_marker`. You can convert *to* Markdown, HTML, Docx, etc. |

## Compatibility Matrix

### General Rule
Most text-based formats (Markdown, HTML, LaTeX, RST, Wiki) can be converted to any other text-based format or Office format (Docx, ODT, PPTX, PDF).

### PDF Logic
- **Input**:
    - Standard PDF input is **not** supported by Pandoc directly with high quality.
    - We use **PDF (High Accuracy)** which converts PDF -> Markdown. From there, you can convert that Markdown to any other format in a second step (or if we implement chaining later). Currently, `pdf_marker` output is **Markdown**.
- **Output**:
    - We generate PDFs using the `pdflatex` engine. This requires the source document to be convertible to LaTeX first (which Pandoc handles).

### Common Conversions
- **Markdown -> PDF**: Excellent.
- **Word (Docx) -> PDF**: Good.
- **HTML -> EPUB**: Excellent.
- **PDF (Image/Scan) -> Markdown**: Uses `pdf_marker`. High accuracy but slower.
