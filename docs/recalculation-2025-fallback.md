# Rate fallback in the private 2025 recalculation

The private file remains the only source of K0–K4 values. Resolve the most specific matching file row first (exact goods/brand, goods wildcard, group wildcard as supported by the existing selector). When a cell is unavailable, use the minimum across all K0–K4 of that planning group and coefficient kind; if the group has no rates, use the minimum across the file for the same kind. This matches the existing model `comparison.Index` group/table minimum rule. Policies and Sales never borrow rates from each other. An exact zero stays zero.

Group/table minima are computed once from the uploaded rate table before joining the sales/plan inputs. An unresolved key keeps its physical ERP group/brand for consistent fact-based policy plan allocation; a matched wildcard retains its file classification. Historical formula lookup, full-year fact allocation, signed turnover, monetary base, payout shares/thresholds and quarterly/annual payment rules stay unchanged. No live model coefficient fallback or repeated preparation of raw Sales is introduced.

The former 50025 guard remains for a genuinely unavailable complete coefficient set after fallback. It no longer rejects an ordinary missing exact match with an available minimum. The minimum fallback was introduced in audit rule `fact25-plan25-v2-minimum-fallback` and is retained in `fact25-plan25-v3-zero-sum-positive-shares`. Old completed output is not represented as having used a newer rule.

After updating the portal, launch the saved 2025 job again. Its uploaded coefficients and plan remain saved; explicit launch installs the updated private procedure and Agent command. No Django migration or changes to ordinary 2026 jobs are needed.

`test_recalculation_2025_fallback` replays all four generated fact/plan lookups and coalescing expressions in SQLite, adapting the dialect-specific APPLY selector and VALUES unpivot. Coverage includes exact K values, wildcard precedence, minimum across all K rather than per segment, separate kinds, physical fallback keys, exact zero and a genuinely missing kind. Actual SQL Server execution must be checked on the portal server.
