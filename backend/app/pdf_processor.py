import io
from dataclasses import dataclass
from typing import List, Optional

from PIL import Image


@dataclass
class PageImage:
    key: str
    content: bytes
    page_number: int


@dataclass
class PDFProcessingResult:
    original_pdf_key: Optional[str] = None
    page_images: List[PageImage] = None
    ocr_text: str = ""
    embedded_text: str = ""

    def __post_init__(self):
        if self.page_images is None:
            self.page_images = []


class PDFProcessor:
    def __init__(self, max_pages: int = 10, dpi: int = 180, jpeg_quality: int = 80, max_size_mb: int = 10):
        self.max_pages = max_pages
        self.dpi = dpi
        self.jpeg_quality = jpeg_quality
        self.max_size_mb = max_size_mb

    def process_pdf(self, pdf_bytes: bytes) -> PDFProcessingResult:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise RuntimeError("PyMuPDF not available")

        if len(pdf_bytes) > self.max_size_mb * 1024 * 1024:
            raise ValueError(f"PDF exceeds size limit of {self.max_size_mb}MB")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")

        if doc.page_count > self.max_pages:
            raise ValueError(f"PDF has {doc.page_count} pages, max allowed {self.max_pages}")

        result = PDFProcessingResult()

        embedded_text_parts = []
        for page_num in range(doc.page_count):
            page = doc[page_num]
            try:
                embedded_text_parts.append(page.get_text())
            except Exception:
                pass

        result.embedded_text = "\n".join(embedded_text_parts)

        for page_num in range(doc.page_count):
            page = doc[page_num]
            try:
                mat = fitz.Matrix(self.dpi / 72.0, self.dpi / 72.0)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                img = img.convert("RGB")
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=self.jpeg_quality, optimize=True)
                result.page_images.append(PageImage(
                    key=f"pages/page-{page_num + 1:03d}.jpg",
                    content=out.getvalue(),
                    page_number=page_num + 1
                ))
            except Exception:
                pass

        return result
