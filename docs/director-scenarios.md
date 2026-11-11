# Director scenario comparison

Open **Motivation → Reports → Director scenario comparison**. Select the separately loaded original Moscow/TT plans for 2025 and 2026, an approved model coefficient version effective no later than 2026, two director rate sources and two recipient directors for 2025. Four distinct directory entries are required; historical/archived entries are selectable. Rates and names remain in the portal database, never in source fixtures.

The saved comparison contains:

1. Full 2025, branches separate: recipient pair receives the source pair's director rates. Only the recipient pair participates in this case. The source pair supplies model-2026 rates only; their own 2025 premiums are not calculated or displayed.
2. Q1–Q3 2026, separate branches, actual turnover.
3. Q1–Q3 2026, separate branches, actual turnover replaced by plan.
4. Q1–Q3 2026, combined Moscow/TT: each source director earns on the entire combined base with their own rates. Recompute execution on combined turnover and plan.
5. The combined branch with actual turnover replaced by plan.

Only the two source directors participate in cases 2–5. Real employee bindings, plans, coefficient tables and live SQL procedures are never changed by this report.

## Sources and accounting

The latest `test_2025` SQL journal must say `completed`, match the selected 2025 plan and fact year, and use the current native 2025 recalculation rule. Both selected plans must have been loaded. For 2026, read the published `PortalPreparedSales` dataset directly: no `all_2026` run or premium output table is required. Validate the completed active preparation, rule, schema and full 2026 date coverage using the ordinary prepared-sales consumer contract. Both inputs are read under the shared `PortalMotivationCalculation` session lock; concurrent publication/exports/calculations are refused. SQL identifiers are fixed constants; values use bound parameters. Connections close on success and failure.

For 2025 use Sales-type physical Fact rows (not director copies or windowed PlanFact turnover), recover original branches through `ПодразделениеДокумент → Subdivisions.SubdivisionKey`, and omit zero-turnover placeholder plan rows. For 2026 join prepared sales to original subdivision, product and customer dimensions, include only goods and dates January–September, preserve HEAT and DPP, and use `Price - AmountUSDIt_ITW + BallsOuterUSD`. Group by the original physical invoice/model identity before separating positive and negative amounts. Resolve O0–O4 against the selected local model version’s discount segments, preserving threshold boundaries, missing-threshold sentinel 10000 and unmatched-type O0 behavior. Multiple matching discount types abort instead of multiplying turnover. Empty model segment configuration or an incompatible/unavailable prepared set abort; there is no raw-Sales or completed-premium fallback. Unknown discount segments or missing original branch recovery abort. Existing branch aliases apply only when resolving model rates and period corrections, never to the original plans/fact branch split. A plan containing only premerged totals cannot be split automatically.

The report preserves the monetary base already calculated in Fact; it does not apply the Split planning base formula. For 100% cases, use the original plan's USD amounts and O0–O4 distribution. No reversal to real deals or eligibility-only override occurs.

Selected model version rates, formula overrides, fallback formula and period corrections apply to director comparisons. Missing exact goods settings use the existing explicit group/table minimum rule; absence of all source-employee rates aborts. For 2025, annual/quarter shares and the payment threshold come from its verified source run. For 2026 they come from the saved main calculation job for the selected plan and year; it need not be enabled, synced or executed. Missing or multiple matching main jobs are explicit errors. Job key/name/values and selected segment thresholds are frozen in the private snapshot and Excel Parameters sheet. Negative invoice accrual is clipped before summing; signed returns still reduce execution.

Q1 has no threshold; Q2 checks Q1+Q2; Q3 checks Q3; Q4 and annual check the full year. Scope follows the selected formula, with the department gate also checked for nonglobal formulas. Nonpositive plan means undefined execution and no conditional payment. Full 2025 includes the annual portion and catch-up of unpaid quarterly accrual when annual conditions pass. In 2026 Q1–Q3 the annual portion is informational accrued money, excluded from payment; there is no Q4 or annual payment/catch-up.

No manager ledger is queried. The 2025 source run is still required for the verified monetary Fact base, discount segments and payout parameters, independently of employee participation. Prepared sales alone do not replace this calculated Fact.

## Persistence and Excel

Every successful calculation saves private input aggregates, rate values/formulas/origins, corrections, source run identifiers, selected plan/version metadata, per-period execution and payments. The browser redirects to the saved snapshot; refresh and Excel use it without querying DWH or recalculating. Failure leaves earlier snapshots intact and labels them as earlier saved results.

Excel has nine sheets: Comparison, five scenario sheets, Execution, Rates, Parameters (Russian labels in the workbook). Dollar amounts and execution percentages are numeric; names/text cannot become Excel formulas. Global administrative/model/finance/general-director roles can calculate. The motivation viewer can read/export only; scoped branch directors cannot access this company-wide comparison.

## Verified inputs and regression coverage

Comparison rule: `director-comparison-five-v4-verified-inputs`. Plan and fact use the same classification: only RAC/VRF/PROF retain goods; only household, semiindustrial, multisplit and VRF retain brands. Other dimensions use `*`. Empty model goods/brand cells and `*` are equivalent exact-match aliases; conflicting alias rates/formulas are rejected rather than selecting an arbitrary row. TT means Thermotrade (`ТЕРМОТРЕЙД`); original branch names are normalized case-insensitively.

Before warehouse access, validate both rate sources against plan goods, including the other branch's goods used by merged scenarios. Before heavy source queries, check all required warehouse columns and report missing names together. Discount thresholds are inserted into a temporary table with bound parameters, without requiring OPENJSON/database compatibility level 130. SQL failures retain the input-reading stage for diagnostics.

Run the regression suite:

```sh
python manage.py test admin_motivation.test_director_prepared_fact admin_motivation.test_director_scenarios admin_motivation.test_director_scenarios_end_to_end admin_motivation.test_director_scenarios_browser admin_motivation.test_director_participation admin_motivation.test_director_formula --settings=portal.settings_local --noinput
```

For the full browser path, set `RUN_BROWSER_TESTS=1` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium`, then run `admin_motivation.test_director_scenarios_browser`. Set `DIRECTOR_VERIFICATION_OUTPUT` to an absolute XLSX path to retain the controlled workbook, JSON totals report and browser screenshot.

The end-to-end fixture replaces only the warehouse connection boundary. Source financial SELECTs execute against a temporary SQLite warehouse with SQL Server syntax adaptation; source readers, classification, rates, five calculations, snapshot persistence, views and nine-sheet Excel are real application code. SQL Server catalog metadata, application locks and readiness metadata are simulated. The Chromium test submits the actual form, checks all five two-director tables, reloads without warehouse reads, and downloads/verifies Excel.

Independent baseline expected USD totals for the two directors are (4,120), (4,72), (24,48), (48,96), (48,96) for cases 1–5. Additional cases cover brandless goods, exact empty-brand rates, missing branch actuals, returns and negative rates, segment-dependent synthetic turnover, annual recovery, missing columns, stale source rules, ambiguous discounts/rates, concurrent access, immutable snapshots and preserving earlier results after failure.

This verifies the application chain locally, not the production DWH or SQL Server execution plan. The SQL Server container image could not be pulled in the verification environment because its registry redirect was blocked by network policy. SQL Server syntax, driver behavior, live source completeness and performance still require a portal-server run. No production data or procedures were changed by these tests.

Discount matching in the comparison gives an exact full type precedence over substring matches. A configured KF_ACC row therefore suppresses generic KF/ACC matches, including when its thresholds are all NULL. Without an exact row, the legacy substring rule remains; multiple matches still fail. Existing SQL procedures and previously saved comparisons are not changed by this reader fix.
