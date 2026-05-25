"""Internal function: extract_document (Vercel Python function).

Downloads a planning PDF and extracts per-page text with PyMuPDF (born-digital
text — cheap, no tokens). For image-based / scanned pages it returns a downscaled
PNG (base64) so the TS ingest orchestrator can transcribe them with Claude vision
(the Claude-native OCR path that replaces Tesseract).

This is an INTERNAL function called by /api/tools/ingest, not a custom tool the
agent calls directly — its output (full page text) stays host-side; only the
ingest manifest reaches the agent.
"""

from __future__ import annotations

import base64
import json
import os
from http.server import BaseHTTPRequestHandler

import httpx

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - present in deployment via requirements.txt
    fitz = None

INTERNAL_TOKEN = os.getenv("INTERNAL_TOOL_TOKEN", "")
USER_AGENT = os.getenv("SCRAPER_USER_AGENT", "BBUG-Planning-Reporter/2.0 (+cycling advocacy)")

MAX_PAGES = 80                 # safety cap (function duration / memory)
MAX_IMAGE_PAGES = 20           # cap base64 payload returned for vision OCR
IMAGE_RENDER_SCALE = 1.4       # ~100→140 dpi; enough for OCR, bounded size
TEXT_PAGE_MIN_CHARS = 40       # below this with images present ⇒ treat as scanned


def _image_ratio(page) -> float:
    try:
        page_area = page.rect.width * page.rect.height
        if page_area <= 0:
            return 0.0
        img_area = 0.0
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if bbox:
                img_area += abs((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        return min(1.0, img_area / page_area)
    except Exception:  # noqa: BLE001
        return 0.0


def _extract(url: str, render_images: bool) -> dict:
    if fitz is None:
        return {"error": "pymupdf not available"}

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=60.0, headers=headers, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        pdf_bytes = resp.content

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = min(doc.page_count, MAX_PAGES)

    pages: list[dict] = []
    page_images: dict[str, str] = {}
    born_digital_chars = 0
    image_pages: list[int] = []

    for i in range(page_count):
        page = doc.load_page(i)
        text = page.get_text("text") or ""
        char_count = len(text.strip())
        born_digital_chars += char_count
        ratio = _image_ratio(page)
        has_images = bool(page.get_images(full=True))
        is_image_based = char_count < TEXT_PAGE_MIN_CHARS and (has_images or ratio > 0.5)

        pages.append(
            {
                "page": i + 1,
                "char_count": char_count,
                "image_ratio": round(ratio, 2),
                "is_image_based": is_image_based,
                "text": text,
            }
        )

        if is_image_based:
            image_pages.append(i + 1)
            if render_images and len(page_images) < MAX_IMAGE_PAGES:
                pix = page.get_pixmap(matrix=fitz.Matrix(IMAGE_RENDER_SCALE, IMAGE_RENDER_SCALE))
                page_images[str(i + 1)] = base64.standard_b64encode(pix.tobytes("png")).decode("ascii")

    doc.close()

    if born_digital_chars > 200 and not image_pages:
        method = "text_layer"
    elif born_digital_chars > 200:
        method = "mixed"
    else:
        method = "image_based"

    return {
        "page_count": page_count,
        "truncated": doc.page_count > MAX_PAGES if doc else False,
        "born_digital_chars": born_digital_chars,
        "image_pages": image_pages,
        "extraction_method": method,
        "pages": pages,
        "page_images": page_images,  # {page_no: base64 png} for scanned pages only
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if INTERNAL_TOKEN and self.headers.get("x-internal-token") != INTERNAL_TOKEN:
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            url = (body.get("document_url") or "").strip()
            if not url:
                self._send(400, {"error": "document_url is required"})
                return
            render = body.get("render_images", True)
            self._send(200, _extract(url, render))
        except httpx.HTTPStatusError as exc:
            self._send(502, {"error": f"document fetch returned {exc.response.status_code}"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
