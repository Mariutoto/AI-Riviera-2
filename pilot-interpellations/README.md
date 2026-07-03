# Interpellation document-metadata pilot

This pilot contains twelve physical PDFs selected across 2024–2026.

- `document_metadata/`: one base document-metadata JSON per physical PDF.
- `scraper_metadata/`: the complete interpellation metadata produced by the existing scraper for the same PDFs.
- `combined_metadata_view/`: the normalized audit view intended to guide the future PostgreSQL shape, with common and interpellation-specific metadata separated.
- `artifacts/`: downloaded PDFs and native extraction text used to calculate `content_hash`.
- `manifest.json`: all twelve base document records in one file.
- `validation_report.json`: non-canonical processing diagnostics.
- `review.html`: visual review table.

The existing metadata under `documents/la-tour-de-peilz/` is not modified.
