from __future__ import annotations

import html
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from municipal_pipeline.documents import REQUIRED_DOCUMENT_FIELDS


def audit_preindex(
    documents: list[dict],
    download_diagnostics: dict,
) -> dict:
    audited_documents = []
    totals = Counter()
    document_roles = Counter()
    for document in documents:
        findings = []
        document_roles[str(document.get("document_role") or "unknown")] += 1

        for field in REQUIRED_DOCUMENT_FIELDS:
            if not document.get(field):
                findings.append(
                    {
                        "severity": "blocker",
                        "code": f"missing_{field}",
                        "message": f"Métadonnée obligatoire absente : {field}",
                    }
                )

        text_audit = document.get("text_audit") or {}
        if text_audit.get("error"):
            findings.append(
                {
                    "severity": "blocker",
                    "code": "pdf_text_extraction_failed",
                    "message": "Le texte du PDF n’a pas pu être extrait.",
                }
            )
        elif text_audit.get("needs_ocr"):
            findings.append(
                {
                    "severity": "blocker",
                    "code": "ocr_required",
                    "message": "Le PDF nécessite un traitement OCR avant indexation.",
                }
            )
        elif int(text_audit.get("text_chars") or 0) < 500:
            findings.append(
                {
                    "severity": "warning",
                    "code": "very_short_text",
                    "message": "Moins de 500 caractères ont été extraits.",
                }
            )

        if int(text_audit.get("empty_pages") or 0):
            findings.append(
                {
                    "severity": "warning",
                    "code": "empty_pages",
                    "message": f"{text_audit['empty_pages']} page(s) sans texte extrait.",
                }
            )

        if not document.get("author"):
            findings.append(
                {
                    "severity": "warning",
                    "code": "missing_author",
                    "message": "Auteur absent de la liste ; à enrichir depuis le PDF.",
                }
            )
        if re.search(r"\.pdf$", str(document.get("title") or ""), flags=re.I):
            findings.append(
                {
                    "severity": "warning",
                    "code": "filename_as_title",
                    "message": "Le site fournit un nom de fichier plutôt qu’un titre éditorial.",
                }
            )
        if len(document.get("listing_occurrences") or []) > 1:
            findings.append(
                {
                    "severity": "info",
                    "code": "duplicate_listing_occurrences",
                    "message": (
                        f"{len(document['listing_occurrences'])} occurrences de la liste "
                        "partagent exactement le même contenu PDF."
                    ),
                }
            )

        pdf_url = str(document.get("pdf_url") or "")
        if urlparse(pdf_url).scheme != "https":
            findings.append(
                {
                    "severity": "warning",
                    "code": "non_https_source",
                    "message": "La source PDF n’utilise pas HTTPS.",
                }
            )

        for finding in findings:
            totals[finding["severity"]] += 1
            totals[finding["code"]] += 1
        audited_documents.append(
            {
                "document_id": document.get("document_id"),
                "title": document.get("title"),
                "listing_date": document.get("listing_date"),
                "author": document.get("author"),
                "document_role": document.get("document_role"),
                "reference": document.get("reference"),
                "pdf_url": document.get("pdf_url"),
                "page_count": text_audit.get("page_count"),
                "text_chars": text_audit.get("text_chars"),
                "text_words": text_audit.get("text_words"),
                "text_preview": text_audit.get("text_preview", ""),
                "findings": findings,
            }
        )

    unrecovered_downloads = [
        item
        for item in download_diagnostics.get("failed_downloads", [])
        if not item.get("equivalent_document_id")
    ]
    source_findings = []
    if download_diagnostics.get("failed_downloads"):
        source_findings.append(
            {
                "severity": "warning" if not unrecovered_downloads else "blocker",
                "code": (
                    "recovered_broken_source"
                    if not unrecovered_downloads
                    else "unrecovered_broken_source"
                ),
                "message": (
                    f"{len(download_diagnostics['failed_downloads'])} lien(s) cassé(s), "
                    f"{download_diagnostics.get('recoverable_failed_downloads', 0)} "
                    "récupéré(s) par un document équivalent."
                ),
            }
        )
    for finding in source_findings:
        totals[finding["severity"]] += 1
        totals[finding["code"]] += 1

    status = (
        "blocked"
        if totals["blocker"]
        else "ready_with_warnings"
        if totals["warning"]
        else "ready"
    )
    return {
        "status": status,
        "summary": {
            "documents": len(documents),
            "blockers": totals["blocker"],
            "warnings": totals["warning"],
            "information": totals["info"],
            "documents_with_findings": sum(
                bool(item["findings"]) for item in audited_documents
            ),
            "finding_codes": {
                key: value
                for key, value in sorted(totals.items())
                if key not in {"blocker", "warning", "info"}
            },
            "document_roles": dict(sorted(document_roles.items())),
        },
        "source_findings": source_findings,
        "documents": audited_documents,
    }


def write_preindex_html(report: dict, path: Path, *, title: str) -> None:
    severity_label = {
        "blocker": "Bloquant",
        "warning": "Avertissement",
        "info": "Information",
    }
    cards = []
    for document in report["documents"]:
        findings = document["findings"]
        finding_html = "".join(
            (
                f'<li class="{finding["severity"]}">'
                f'<strong>{severity_label[finding["severity"]]}</strong> — '
                f'{html.escape(finding["message"])}</li>'
            )
            for finding in findings
        ) or "<li>Aucune anomalie.</li>"
        card = f"""
            <article>
              <h3>{html.escape(str(document["title"]))}</h3>
              <p><code>{html.escape(str(document["document_id"]))}</code></p>
              <p>{html.escape(str(document.get("listing_date") or ""))}
                 · {html.escape(str(document.get("author") or "Auteur non renseigné"))}
                 · {document.get("page_count") or 0} page(s)
                 · {document.get("text_chars") or 0} caractères</p>
              <ul>{finding_html}</ul>
              <details><summary>Aperçu du texte</summary>
                <p>{html.escape(str(document.get("text_preview") or ""))}</p>
              </details>
              <p><a href="{html.escape(str(document["pdf_url"]))}">Source PDF</a></p>
            </article>
            """
        cards.append("\n".join(line.rstrip() for line in card.splitlines()))
    summary = report["summary"]
    source_findings = "".join(
        f"<li>{html.escape(item['message'])}</li>"
        for item in report["source_findings"]
    ) or "<li>Aucune anomalie de source.</li>"
    page = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font:15px/1.5 system-ui;margin:0;background:#f4f6f9;color:#182234}}
main{{max-width:1050px;margin:auto;padding:24px}}
.hero,article{{background:white;border:1px solid #dbe1ea;border-radius:12px;padding:18px;margin:14px 0}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
.stat{{background:#edf3fb;padding:12px;border-radius:9px}}.stat strong{{display:block;font-size:1.6rem}}
.blocker{{color:#a51d24}}.warning{{color:#8a5700}}.info{{color:#225d91}}
code{{word-break:break-all}}summary{{cursor:pointer;font-weight:650}}
@media(max-width:700px){{.stats{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>
<section class="hero"><h1>{html.escape(title)}</h1>
<p>Décision : <strong>{html.escape(report["status"])}</strong></p>
<div class="stats">
<div class="stat"><strong>{summary["documents"]}</strong>documents</div>
<div class="stat"><strong>{summary["blockers"]}</strong>bloquants</div>
<div class="stat"><strong>{summary["warnings"]}</strong>avertissements</div>
<div class="stat"><strong>{summary["information"]}</strong>informations</div>
</div><h2>Sources</h2><ul>{source_findings}</ul></section>
{''.join(cards)}
</main></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
