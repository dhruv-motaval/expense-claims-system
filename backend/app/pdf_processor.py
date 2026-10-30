"""PDF processing: embedded-text extraction and page rendering.

Per the current storage architecture, page images for NEW uploads are produced
transiently in memory only when a caller asks for them (legacy artifact flows);
the canonical work here is embedded text + accurate page_count. Rendering is
opt-in via `render_pages` because its output is discarded by the canonical path
and it is the most expensive step per upload.
"""
import io
from dataclasses import dataclass, field
from typing import List, Optional
@dataclass
class PageImage:
    key: str
    content: bytes
    page_number: int


@dataclass
class PDFProcessingResult:
    embedded_text: str = ""
    page_count: int = 0
    page_images: Optional[List[PageImage]] = None

    def __post_init__(self):
        if self.page_images is None:
            self.page_images = []


class PDFProcessor:
    def __init__(self, max_pages=10, dpi=180, jpeg_quality=80, max_size_mb=10):
        self.max_pages = max_pages
        self.dpi = dpi
        self.jpeg_quality = jpeg_quality
        self.max_size_mb = max_size_mb

    def process_pdf(self, pdf_bytes: bytes, render_pages: bool = False) -> PDFProcessingResult:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise RuntimeError("PyMuPDF not available")

        if len(pdf_bytes) > self.max_size_mb * 1024 * 1024:
            raise ValueError(f"PDF exceeds size limit of {self.max_size_mb}MB")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            if doc.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported")

            if doc.page_count > self.max_pages:
                raise ValueError(f"PDF has {doc.page_count} pages, max allowed {self.max_pages}")

            result = PDFProcessingResult(page_count=doc.page_count)

            embedded_text_parts = []
            for page_num in range(doc.page_count):
                page = doc[page_num]
                try:
                    embedded_text_parts.append(page.get_text())
                except Exception:
                    pass

            result.embedded_text = "\n".join(embedded_text_parts)

            if not render_pages:
                return result

            from PIL import Image  # only needed when a caller wants page images

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
        finally:
            # MuPDF documents hold native resources; leak nothing per upload.
            try:
                doc.close()
            except Exception:
                pass
