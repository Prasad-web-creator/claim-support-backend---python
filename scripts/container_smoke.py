"""Verify health routing and real OCR using only generated sample data."""

import asyncio
from time import perf_counter

import fitz
from httpx import ASGITransport, AsyncClient

from app.services.extraction.document_extraction import extract_text_from_document
from main import app


async def verify() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://internal") as client:
        response = await client.get("/health")
        assert response.status_code == 200 and response.json()["status"] == "healthy"

    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 100), "Diagnosis: Synthetic verification", fontsize=24)
        image = page.get_pixmap(dpi=160).tobytes("png")
    started = perf_counter()
    result = await extract_text_from_document(image, "image/png")
    assert "synthetic verification" in result.casefold()
    print(f"Health and real OCR passed. OCR took {perf_counter() - started:.2f} seconds.")


if __name__ == "__main__":
    asyncio.run(verify())
