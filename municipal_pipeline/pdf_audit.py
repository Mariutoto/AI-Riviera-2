from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

import requests

from municipal_pipeline.documents import validate_document


def inspect_pdf_text(content: bytes) -> dict:
    try:
        import fitz

        with fitz.open(stream=content, filetype="pdf") as document:
            page_texts = [page.get_text("text") for page in document]
            text = "\n".join(page_texts)
            page_count = document.page_count
        text_chars = len(text.strip())
        normalized_preview = re.sub(r"\s+", " ", text).strip()[:1000]
        return {
            "page_count": page_count,
            "text_chars": text_chars,
            "text_words": len(re.findall(r"\S+", text)),
            "empty_pages": sum(not page_text.strip() for page_text in page_texts),
            "page_text_chars": [len(page_text.strip()) for page_text in page_texts],
            "text_preview": normalized_preview,
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "needs_ocr": text_chars < max(200, page_count * 50),
        }
    except Exception as exc:
        return {
            "page_count": None,
            "text_chars": 0,
            "text_words": 0,
            "empty_pages": None,
            "page_text_chars": [],
            "text_preview": "",
            "text_hash": "",
            "needs_ocr": True,
            "error": repr(exc),
        }


def audit_pdf_documents(
    items: list[dict],
    *,
    document_id_prefix: str,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
    download_dir: Path | None = None,
    occurrence_fields: tuple[str, ...] = (
        "listing_date",
        "author",
        "reference",
        "title",
        "pdf_url",
        "source_download_id",
    ),
    normalize_title: Callable[[str], str] = str.casefold,
) -> tuple[list[dict], dict]:
    client = session or requests.Session()
    by_hash: dict[str, dict] = {}
    failed = []
    for index, item in enumerate(items, start=1):
        validate_document(item)
        try:
            response = client.get(
                item["pdf_url"], headers=headers or {}, timeout=45
            )
            response.raise_for_status()
            content = response.content
            if not content.startswith(b"%PDF"):
                raise ValueError("la réponse téléchargée n'est pas un PDF")
            content_hash = hashlib.sha256(content).hexdigest()
            is_new_document = content_hash not in by_hash
            canonical = by_hash.setdefault(
                content_hash,
                {
                    **item,
                    "document_id": f"{document_id_prefix}_{content_hash[:20]}",
                    "content_hash": content_hash,
                    "content_bytes": len(content),
                    "listing_occurrences": [],
                },
            )
            if is_new_document:
                canonical["text_audit"] = inspect_pdf_text(content)
            canonical["listing_occurrences"].append(
                {
                    key: item.get(key, "")
                    for key in occurrence_fields
                }
            )
            if download_dir:
                download_dir.mkdir(parents=True, exist_ok=True)
                target = download_dir / f"{canonical['document_id']}.pdf"
                if not target.exists():
                    target.write_bytes(content)
            print(
                f"{index}/{len(items)} "
                f"{item.get('source_download_id') or item['pdf_url']}",
                flush=True,
            )
        except Exception as exc:
            failed.append(
                {
                    "pdf_url": item["pdf_url"],
                    "source_download_id": item.get("source_download_id", ""),
                    "listing_date": item.get("listing_date", ""),
                    "title": item["title"],
                    "error": repr(exc),
                }
            )

    documents = sorted(
        by_hash.values(), key=lambda item: item.get("listing_date", ""), reverse=True
    )
    document_by_title = {
        normalize_title(item["title"]): item["document_id"] for item in documents
    }
    for failure in failed:
        replacement = document_by_title.get(normalize_title(failure["title"]))
        if replacement:
            failure["equivalent_document_id"] = replacement
    recoverable_failures = sum("equivalent_document_id" in item for item in failed)
    diagnostics = {
        "downloaded_occurrences": len(items) - len(failed),
        "canonical_pdf_documents": len(documents),
        "duplicate_listing_occurrences": sum(
            max(0, len(item["listing_occurrences"]) - 1) for item in documents
        ),
        "documents_needing_ocr": sum(
            bool((item.get("text_audit") or {}).get("needs_ocr"))
            for item in documents
        ),
        "recoverable_failed_downloads": recoverable_failures,
        "failed_downloads": failed,
        "complete": not failed,
        "usable_complete": recoverable_failures == len(failed),
    }
    return documents, diagnostics
