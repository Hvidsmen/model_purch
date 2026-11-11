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

## Narrow consumer copies and daily timing

Consumers retain only Sales columns whose identifiers occur in the remaining SQL or input scope, instead of copying unused wide fields into tempdb for every variant. Metadata comes from `dbo.Sales`; unqualified financial fields are included too. The original-author service field is preserved for historical calculations. Unknown metadata, direct star consumers and dynamic SQL retain the full original projection. This does not change prepared-set publication, readiness/schema checks, coefficient rules or turnover arithmetic.

The known year predicate now uses a half-open date range, retaining the original lower bound and replacing YEAR with the next January 1 upper bound. The producer already creates `IX_PortalPreparedSales_Date`. Each consumer prints copied rows, projected-column count and preparation/copy milliseconds into Agent output. Overall Agent duration can include sequential combined/separate calculations; nested journal durations must not be blindly summed.

After deploying, save the existing daily job parameters once to regenerate all consumer procedures and the Agent command. There is no need to rebuild the prepared Sales dataset. The guarded source-compatibility check remains; if it fails, the transaction must not replace the old procedures/command. `admin_motivation/sql/diagnostics/daily_prepared_sales.sql` is read-only and checks actual installed markers, recent per-variant durations and saved command composition.

Local checks cover date boundaries and index eligibility, unused wide payload omission, retained unqualified monetary inputs, returns and author fields, conservative fallback, legacy compatibility and the chained job. Production SQL Server performance is not measured in the development environment; neither a speedup factor nor the cause of an earlier slow run is inferred from these tests.

## Live daily Agent monitoring

The calculation card polls the light endpoint (`light=1`), avoiding the full coefficient/workflow fingerprint rebuild every ten seconds. While Agent activity is running, the monitor additionally reads only that job's active T-SQL request. The match uses Agent's program-name binary job identifier; no unscoped request text is exposed. Current statement offsets and the active procedure identify prepared-copy, coefficients, fact, plan/fact, premium, annual and result-registration phases. Combined/separate procedure names identify the active scenario.

The UI shows the actual Agent start and elapsed time, an indeterminate activity bar, and lock/I/O/parallel wait descriptions. It does not infer a percent or call a known stage when the statement is unrecognized. Missing VIEW SERVER STATE/VIEW SERVER PERFORMANCE STATE or a request transition preserves the Agent status with an explicit unavailable-stage message. Finished jobs do not query request DMVs. Polling does not acquire calculation locks, alter jobs, refresh statistics or write financial data. This works for an already running Agent job after the web code is updated; saving/restarting that job is not required.

This optimizes monitoring, not an unobserved financial SQL plan. The previous consumer projection/date-range change applies after job synchronization; further SQL tuning should use the active operation/wait and a measured plan rather than adding speculative indexes to a live run.

## Stage performance diagnostics

New runs store bounded session snapshots in `motivation.PortalCalculationStageProfile`, keyed by audit RunId and stage sequence. Preparation distinguishes sales copy, document selection, document attributes, discount calculation, validation/index, and publication. Native 2025 distinguishes its existing eight checkpoints plus initialization. The report and preparation monitor display elapsed seconds, CPU milliseconds, logical/physical reads and writes. Counters are active-request deltas, including instrumentation overhead; they are not operator-level execution-plan measurements. Missing counters are NULL, not zero. CPU time may exceed wall time with parallel work. Current waits/blockers are queried separately while Agent is running; the displayed wait duration is the current wait, not a stage total. Missing DMV privileges do not prevent calculation or show SQL text.

Snapshots are session-context values, cleared at the start of each run. Their persistence happens after financial COMMIT or handled ROLLBACK. Completed stages survive a handled error; only the final reached stage is marked failed. A killed connection may leave no stage profile. Historical runs are not reconstructed. No financial expressions, source Sales rows, allocation rules or payout rules change.

After updating, explicitly prepare the sales dataset again to install its new producer procedure (saving daily parameters also installs it). A new explicit native-2025 launch installs its own instrumentation. Do not interrupt an existing run to collect measurements. Compare the same data/parameters before optimizing the stage with the highest measured cost. SQL Server compilation and production performance require verification on the actual server; local tests validate generated SQL structure and UI/data handling.

Checkpoint counters now read `sys.dm_exec_requests` for `@@SPID` and `CURRENT_REQUEST_ID()`. Session-level counters can stay unchanged until the encompassing batch finishes, which produced zero deltas during the long procedure. Old persisted profiles are not rewritten. Request counters (especially parallel I/O counters) are diagnostic hints, not a substitute for actual execution plans or STATISTICS IO/TIME.

## Discount-stage FX cache

A measured run spent 648.037 of 720.334 seconds in discount calculation. The producer now materializes distinct invoice-date/currency pairs used by empty price-type sales, evaluates `getCurrancyRateUSD` once per pair, and joins the typed results into the original discount CASE. Historical-price lookup on the UPDATE is restricted to empty price types; all other CASE branches never use its values. The key selection and UPDATE retain every historical-price predicate and inclusive date boundaries. A null-safe currency join retains NULL-currency function behavior. No casts or rounding are introduced. Source canonical preparation and consumer compatibility checks are unchanged.

Validation executes the generated original and cached SELECT/CASE on SQLite fixtures (missing/zero rates and prices, NULL currency, no matching price, empty/nonempty/NULL price type, zero quantities and amounts, 100% discount, bonuses, inclusive date endpoints) and checks result equality and one UDF invocation per materialized key. This validates relational/CASE behavior; it does not replace SQL Server compilation, decimal-type validation or a production benchmark. The next explicit preparation installs this producer. Compare the next measured discount stage against 648.037 seconds with comparable source data.
