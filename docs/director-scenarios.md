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

The latest `test_2025` and `all_2026` SQL run journals must say `completed` and match the selected plan source versions and fact years. Both source tables are read under the shared `PortalMotivationCalculation` session lock; concurrent exports/calculations are refused. SQL identifiers are fixed allowlisted constants; values use bound parameters. Connections close on success and failure.

Use Sales-type physical Fact rows (not director copies or windowed PlanFact turnover), recover original branches through `ПодразделениеДокумент → Subdivisions.SubdivisionKey`, and omit zero-turnover placeholder plan rows. Unknown discount segments or missing original branch recovery abort. Existing branch aliases apply only when resolving model rates and period corrections, never to the original plans/fact branch split. A plan containing only premerged totals cannot be split automatically.

The report preserves the monetary base already calculated in Fact; it does not apply the Split planning base formula. For 100% cases, use the original plan's USD amounts and O0–O4 distribution. No reversal to real deals or eligibility-only override occurs.

Selected model version rates, formula overrides, fallback formula and period corrections apply to director comparisons. Missing exact goods settings use the existing explicit group/table minimum rule; absence of all source-employee rates aborts. Annual/quarter shares and payment threshold come from each verified year's source run. Negative invoice accrual is clipped before summing; signed returns still reduce execution.

Q1 has no threshold; Q2 checks Q1+Q2; Q3 checks Q3; Q4 and annual check the full year. Scope follows the selected formula, with the department gate also checked for nonglobal formulas. Nonpositive plan means undefined execution and no conditional payment. Full 2025 includes the annual portion and catch-up of unpaid quarterly accrual when annual conditions pass. In 2026 Q1–Q3 the annual portion is informational accrued money, excluded from payment; there is no Q4 or annual payment/catch-up.

No manager ledger is queried. The 2025 source run is still required for the verified monetary Fact base, discount segments and payout parameters, independently of employee participation. Prepared sales alone do not replace this calculated Fact.

## Persistence and Excel

Every successful calculation saves private input aggregates, rate values/formulas/origins, corrections, source run identifiers, selected plan/version metadata, per-period execution and payments. The browser redirects to the saved snapshot; refresh and Excel use it without querying DWH or recalculating. Failure leaves earlier snapshots intact and labels them as earlier saved results.

Excel has nine sheets: Comparison, five scenario sheets, Execution, Rates, Parameters (Russian labels in the workbook). Dollar amounts and execution percentages are numeric; names/text cannot become Excel formulas. Global administrative/model/finance/general-director roles can calculate. The motivation viewer can read/export only; scoped branch directors cannot access this company-wide comparison.

Run Django tests `admin_motivation.test_director_scenarios`; optionally set `RUN_BROWSER_TESTS=1` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE` for the saved-result/download Chromium test. Local SQL input tests use a fake cursor; actual DWH connectivity and source-run availability must be checked on the portal server.
