"""Optional local OCR fallback for scanned PDFs (no external services).

Requires the optional system dependency Tesseract:
    Windows: winget install UB-Mannheim.TesseractOCR   (then ensure tesseract.exe is on PATH)
    Debian/Ubuntu: apt-get install tesseract-ocr
    macOS: brew install tesseract

This module is only imported when a PDF has no embedded text. Normal tests do
not depend on a Tesseract installation; when pytesseract/Tesseract is missing
the caller falls back to manual entry.
"""
import io


def ocr_pdf_pages(pdf_bytes: bytes, dpi: int = 180) -> str:
    """OCR each PDF page locally and return combined text preserving page order."""
    import io

    import fitz  # PyMuPDF
    from PIL import Image

    try:
        import pytesseract
    except ImportError:
        raise RuntimeError("pytesseract is not installed")

    parts: list[str] = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_num in range(doc.page_count):
            page = doc[page_num]
            mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            text = pytesseract.image_to_string(img, lang="eng")
            if text.strip():
                parts.append(f"--- page {page_num + 1} ---\n{text.strip()}")
    finally:
        doc.close()
    return "\n".join(parts)
