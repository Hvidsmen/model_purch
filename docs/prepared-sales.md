# Shared prepared fact sales

The portal now builds one DataWH sales dataset from 2025-01-01 through the end of the current server calendar year. Preparation enriches document attributes and motivation discounts using the existing reviewed preparation SQL. It includes original document subdivision keys for all subdivisions. Old invoices and corrections are matched by document/product keys, not restricted to the year of their invoice dates.

The common dataset contains no premium coefficients, director roster or subdivision merging. Each calculation applies its existing rules and branch scope after selecting its fact year from the prepared dataset. Main 2026, all quarters, separate Moscow/TT and the private 2025 calculation use this path. The original warehouse procedure and dbo.Sales are not modified. The 2025 author fallback retains the original Sales author when the matched document author is empty; other variants keep their original behavior.

## First setup after updating the portal

1. Open Motivation → Administration → Calculation Center.
2. In **Подготовка факта продаж с 2025 года**, click **Подготовить сейчас**. This installs the dedicated SQL Agent procedure/job and a daily schedule at **08:00 Moscow**, converted to SQL Server's current local UTC offset. The browser does not need to remain open.
3. Wait for the ready dataset version and row count to appear. Preparation runs as an independent SQL Agent task; the page polls read-only metadata every ten seconds.
4. Restart the required 2025/2026 calculations. Existing scheduled main jobs must be synchronized once after the software update by saving their parameters or launching them manually; this rewrites their portal procedures/Agent command to consume prepared sales.

The schedule must follow the corporate Sales/ERP load. The portal lock coordinates portal preparation, calculation and export; it does not lock out unrelated corporate ETL. Server timezone changes require schedule review. The center shows the preparation separately from the list of premium calculation jobs.

## Publication and failure behavior

The producer acquires the same session application lock as portal calculations. Preparation and a premium calculation cannot run concurrently; a competing start fails with a busy message rather than mixing dataset versions. No calculation silently falls back to repeating the old enrichment.

Preparation writes an unpublished staging table directly, avoiding a second full multi-year copy from tempdb. Document matching collapses rows only when all six attributes used by enrichment are equivalent (including the existing empty-string normalization for the three price/discount fields). Different technical references or row IDs alone do not cause failure. Different business attributes still fail with error 50005; up to twenty conflicting keys and original document/attribute XML samples are stored privately in motivation.PortalPreparedSalesConflict for that RunId. Missing delivery values (NULL/empty) are normalized using ISNULL to retain the original column type. An incomplete delivery row is removed only if an existing row with identical prices, discount type and author contains all its filled delivery values plus additional filled delivery information. Conflicting filled values and complementary partial rows remain errors: no fabricated delivery tuple is synthesized. Author NULL/empty distinctions and nonempty binary references are retained. Delivery attributes are not consumed by the reviewed premium formulas. Preparation reports progress stages and checks source schema stability and a nonempty result. An indexed staging table replaces the active table, active version pointer and completed-run metadata in one SQL transaction. An error before or during publication retains the previous active dataset. A cancelled build can leave an unpublished staging table; the next locked build replaces it. Monitoring checks Agent activity so cancellation does not permanently disable the manual preparation button.

Consumers require a completed dataset with the supported preparation rule, matching source column metadata and coverage of the entire requested year. Missing/incompatible preparation produces an explicit error directing the operator to prepare it first. A previous successful dataset remains usable after a failed refresh; its date/version are displayed rather than presented as a new refresh. Inputs are refreshed in full daily, not incrementally.

Each SQL calculation journal entry records `prepared_sales_run` and `prepared_sales_rule`. The entire chained calculation holds the publication lock, so all variants in that chain use the same snapshot. The saved journal provides provenance even after the next successful refresh replaces the active dataset.

## Deployment boundaries and validation

Procedures are generated from the reviewed SQL template. Before replacing a calculation's enrichment, preparation tokens are compared with the common definition (preserving SQL literals and discount mathematics). An unknown preparation shape fails compatibility validation. The preexisting 2025 historical-author exception is handled explicitly.

No Django migration or source configuration secrets are needed. SQL setup occurs only during explicit preparation start or calculation-job synchronization, never on opening the center or polling its monitor. Existing DataWH/Agent permissions are required, including creating tables/procedures, renaming the staging table, configuring jobs and reading Agent activity/history.

The service and tests do not assume real warehouse access in the development environment. Tests cover SQL generation, scopes and temporal guards, publication ordering/rollback boundaries, saved snapshot provenance, registration/time conversion, error handling, cancellation, authorization and the browser card. Generated SQL still needs first-run validation on the actual SQL Server; no real performance duration is claimed by local tests.

## Reviewed legacy helper compatibility

The warehouse may keep document enrichment in parameterless `sp_PrepareSales`, without the old duplicate guard/index and with the original eight-branch discount CASE. The shared producer still performs its stronger independent deduplication/conflict validation. Consumers may omit the old guard/index only when the remainder of the document update and discount preparation matches the reviewed contract.

The reviewed legacy CASE uses `/100` and guarded raw division; the shared CASE uses `/100.0` and defensive NULLIF. Only this whole known CASE is accepted. Before substituting it, preparation checks `dbo.Sales.Discount.system_type_id`: it must be real (59) or float (62), which makes both numeric literals floating operands of the same value. Integer, decimal, money, unknown metadata and an unavailable metadata cursor are rejected. Other changed guards, monetary terms, document fields or price-history joins remain incompatible. There is no generic removal of NULLIF or weakening of financial comparison.

This is a consumer compatibility change; the producer formula and prepared dataset rule are unchanged. Existing compatible ready snapshots remain usable. Updating the portal and launching the saved main job installs the updated private consumers; no rebuild of the ready sales set, live warehouse procedure change or Django migration is required.

`test_prepared_sales_compatibility` covers both floating types, rejected nonfloating/missing types, altered formulas/guards/joins, both CASE expressions on NULL/zero/return and randomized floating inputs, and helper expansion through main/all/separate 2026 preparation. Uploaded procedure definitions are used only for private comparison, never copied into public fixtures.
