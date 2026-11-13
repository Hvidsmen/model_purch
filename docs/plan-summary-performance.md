# Saved sales-plan summary performance

Migration 0040 adds a (scenario_id, subdivision) index on SalesPlanLine. Global summaries select subdivision="" within a scenario; distinct subdivision checks can use this compact index instead of traversing wider plan rows. Existing snapshots, calculation rules and money fields are unchanged.

The report reuses its excluded-subdivision set and skips the global SQL aggregate entirely when a reconstructed participating-branch snapshot is required. Previously that aggregate was executed and then discarded. HTML and CSV request a compact projection; detailed audit exports keep the full projection. Compact mode streams saved original rate snapshots in batches of 128, omits coefficient-version joins and avoids retaining/copying full audit JSON. It still uses saved rates and original branch segment amounts, with identical exclusion, director, missing-DDP and unknown-rate semantics. The original stored calculation JSON is never modified. No cross-request result cache is introduced.

Regression checks compare totals, grouped rows and hierarchy between compact and audit projections, including excluded directors, missing DDP and missing historical rates. Query checks verify one subdivision-name scan, one participating-input read and one original-rate read, with no discarded aggregate, extra join or deferred-field queries.

A local synthetic SQLite measurement with tracemalloc used 1,284 plan rows (321 dated global snapshots with 1,000 extra audit records per snapshot): full audit projection 4.297 seconds / 163.87 MiB peak Python allocations; compact projection 1.209 seconds / 19.13 MiB. Totals matched. This measures the projection workload on artificial data, not production response time; it does not measure DWH calculation or plan import speed.


Further cold-open optimization (2026-11-13): compact SQLite projections use a single multipath json_extract per saved global audit document to fetch ten policy/sales coefficients and the director employee array. Python no longer decodes unrelated coefficient provenance or audit fields. Other database backends keep the existing bounded iterator. Source director bases are computed once per row instead of five times, with financial/exclusion and missing-rate semantics unchanged. No cache, migration, reload or recalculation is required.

A synthetic local SQLite projection benchmark used 64 global snapshots, each with 5,000 unused audit entries: the original iterator took 0.857 seconds and 126.19 MiB peak Python allocations; the compact projection took 0.025 seconds and 0.27 MiB. This measures only saved-rate projection, not complete portal request latency or production DWH operations.
