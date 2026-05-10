# api-gateway/services/pdf_extractor.py
# Module A — PDF text and metadata extraction via PyMuPDF

import fitz  # PyMuPDF
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PDFExtractor:
    """
    Extracts structured content from PDF files using PyMuPDF.

    Handles: text blocks, headings (heuristic by font size),
    metadata, image detection, page count.

    Intentionally does NOT call any LLM — this is pure extraction.
    LLM steps happen in tasks/ingestion.py after extraction.
    """

    def __init__(self, file_path: str):
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF not found: {file_path}")
        self.file_path = file_path

    def extract(self) -> dict:
        """Extract all content. Returns structured dict."""
        doc = fitz.open(self.file_path)

        try:
            metadata = self._extract_metadata(doc)
            pages = []
            all_text_parts = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                page_data = self._extract_page(page, page_num + 1)
                pages.append(page_data)
                all_text_parts.append(page_data["text"])

            full_text = "\n\n".join(all_text_parts)
            word_count = len(full_text.split())

            logger.info(
                f"Extracted {len(doc)} pages, {word_count} words from {self.file_path}"
            )

            return {
                "page_count": len(doc),
                "word_count": word_count,
                "metadata": metadata,
                "pages": pages,
                "full_text": full_text,
                "has_images": any(p["image_count"] > 0 for p in pages),
            }

        finally:
            doc.close()

    def _extract_metadata(self, doc: fitz.Document) -> dict:
        """Extract PDF metadata (title, author, creation date, etc.)."""
        meta = doc.metadata or {}
        return {
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "subject": meta.get("subject", ""),
            "creator": meta.get("creator", ""),
            "creation_date": meta.get("creationDate", ""),
            "modification_date": meta.get("modDate", ""),
            "page_count": len(doc),
            "file_size_bytes": os.path.getsize(self.file_path),
            "is_encrypted": doc.is_encrypted,
        }

    def _extract_page(self, page: fitz.Page, page_num: int) -> dict:
        """Extract content from a single page."""
        # Get text with layout preservation
        text = page.get_text("text").strip()

        # Detect headings via font size heuristic
        blocks = page.get_text("dict")["blocks"]
        headings = self._detect_headings(blocks)

        # Count images
        image_list = page.get_images(full=True)

        return {
            "page_number": page_num,
            "text": text,
            "headings": headings,
            "image_count": len(image_list),
            "char_count": len(text),
            "word_count": len(text.split()),
        }

    def _detect_headings(self, blocks: list) -> list:
        """
        Heuristic heading detection: text spans with font size significantly
        larger than document average are likely headings.
        """
        headings = []
        all_sizes = []

        for block in blocks:
            if block.get("type") == 0:  # Text block
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        all_sizes.append(span.get("size", 12))

        if not all_sizes:
            return headings

        avg_size = sum(all_sizes) / len(all_sizes)
        heading_threshold = avg_size * 1.3  # 30% larger than average

        for block in blocks:
            if block.get("type") == 0:
                for line in block.get("lines", []):
                    line_text = ""
                    max_size = 0
                    is_bold = False
                    for span in line.get("spans", []):
                        line_text += span.get("text", "")
                        size = span.get("size", 12)
                        if size > max_size:
                            max_size = size
                        # Bold detection via font flags
                        if span.get("flags", 0) & 2**4:
                            is_bold = True

                    line_text = line_text.strip()
                    if line_text and (max_size >= heading_threshold or is_bold):
                        headings.append({
                            "text": line_text,
                            "font_size": max_size,
                            "is_bold": is_bold,
                        })

        return headings