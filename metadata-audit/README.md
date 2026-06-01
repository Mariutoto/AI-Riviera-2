# Metadata Audit Samples

Ten representative JSON metadata files copied from `documents/la-tour-de-peilz/`.

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
8. `08-motion-postulat.json`
9. `09-rapport-gestion.json`
10. `10-institutionnel.json`

For each file, decide:

- canonical `city`
- canonical `doc_type`
- canonical `document_date`
- canonical `source_url`
- whether `title` is good enough
- whether `summary` is useful
- which fields are stable enough for hashes
