import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pymupdf

from fin_assistant.config import settings
from fin_assistant.extract.chunking import normalize_whitespace
from fin_assistant.extract.pdf import PdfText, PdfTextError, _readable

PROMPT = (
    "Transcribe all text on this page exactly, in the original language. "
    "Keep table rows on separate lines with columns separated by ' | '. Output only the text."
)
PARALLEL_PAGES = 8
MAX_TOKENS = 4000
RENDER_DPI = 150


def _cache_path(pdf: Path) -> Path:
    key = hashlib.md5(f"{pdf.name}:{pdf.stat().st_size}".encode()).hexdigest()[:12]
    return settings.processed_dir / "ocr" / f"{pdf.stem}_{key}.json"


def _ocr_image(data: bytes, mime: str) -> str:
    r = httpx.post(
        f"{settings.llm_base_url}/chat/completions",
        timeout=300,
        json={
            "model": settings.llm_model,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/{mime};base64,{base64.b64encode(data).decode()}"
                            },
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        },
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def _page_image(page) -> tuple[bytes, str] | None:
    try:
        return page.get_pixmap(dpi=RENDER_DPI).tobytes("png"), "png"
    except Exception:
        return None


def ocr_pdf(path: Path) -> PdfText:
    cache = _cache_path(path)
    pages = None
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        pages = [tuple(p) for p in cached["pages"]]
        if not any(_readable(t) for _, t in pages) and cached.get("model") != settings.llm_model:
            pages = None
    if pages is None:
        try:
            with pymupdf.open(path) as doc:
                images = [_page_image(p) for p in doc]
        except Exception as e:
            raise PdfTextError(f"{path.name}: cannot read PDF: {e}") from e

        def work(item: tuple[int, tuple[bytes, str] | None]) -> tuple[int, str]:
            number, img = item
            if img is None:
                return number, ""
            try:
                return number, normalize_whitespace(_ocr_image(*img).replace("\x00", ""))
            except httpx.HTTPError:
                return number, ""

        with ThreadPoolExecutor(max_workers=PARALLEL_PAGES) as pool:
            pages = list(pool.map(work, enumerate(images, 1)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {"source": str(path), "model": settings.llm_model, "pages": pages}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
    usable = [(n, t) for n, t in pages if _readable(t)]
    if not usable:
        raise PdfTextError(f"{path.name}: OCR gave no readable text")
    digest = hashlib.sha256("\n".join(t for _, t in usable).encode("utf-8")).hexdigest()
    return PdfText(usable, len(pages), digest)
