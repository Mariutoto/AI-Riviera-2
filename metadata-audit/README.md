# Metadata Audit Samples

Twelve representative JSON metadata files copied from `documents/la-tour-de-peilz/`.

Use these samples to decide which fields are canonical, which fields should stay in
flexible metadata, and which fields should participate in document/chunk hashing.

Suggested review order:

1. `01-budget.json`
2. `02-preavis-municipal.json`
3. `03-proces-verbal.json`
4. `04-ordre-du-jour.json`
5. `05-communication-municipale.json`
6. `06-information-diverse.json`
7. `07-info-municipalite.json`
8. `08-motion.json`
9. `09-postulat.json`
10. `10-interpellation.json`
11. `11-rapport-gestion.json`
12. `12-institutionnel.json`

For each file, decide:

- canonical `city`
- canonical `doc_type`
- canonical `document_date`
- canonical `source_url`
- whether `title` is good enough
- whether `summary` is useful
- which fields are stable enough for hashes

Enhanced files (`*.enriched.json`) are proposed target shapes for richer metadata.
They are examples for review first; they are not wired into ingestion yet.
Keep them in `metadata-audit/` until the ingestion schema is intentionally updated;
do not copy these enriched examples into the yearly `documents/` folders as source data.
