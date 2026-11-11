# Plans without exact historical author fact in 2025

For a policy plan choose one whole author-share scope in this order, always within the original document subdivision and fact year 2025:

1. Exact coefficient-classified planning group, goods group and brand.
2. Same planning group and goods group, ignoring brand.
3. Same planning group, ignoring goods/brand.
4. Entire original subdivision.

Do not choose a different level per author or combine all levels: that would multiply the plan. At each level preserve the existing signed-author-net / signed-total rule. If its signed total is zero, distribute by positive author nets, with nonpositive author nets receiving zero. A scope with no nonzero author nets cannot provide weights; try the next level.

Broader historical pools use only eligible policy fact: exclude HEAT, DPP channel and the same excluded subdivision names as the premium calculation. Sales or other years never supply policy-author weights. Canonical classification (including empty mobile brand), file exact/wildcard/minimum rate lookup and original subdivision separation remain unchanged. No current employee roster or another subdivision supplies authors.

If no scope supplies historical weights, preserve the full policy plan once as author “Не распределён”. It has no factual premium base and therefore zero premium; other calculations continue. The plan remains in PlanFact and department execution inputs. It is visible in the existing performance Excel sheet with an explicit explanation. The established Q1 display normalization remains unchanged. Missing K0–K4 still stops the run; absence of author shares is no longer treated as a coefficient error.

Exact shares are computed as before. Broader shares aggregate a small eligible author/goods grid once, then compute three rollups; they do not rerun Sales enrichment or scan the raw warehouse source. Scope matching is indexed where the actual temp column types support a safe index. Audit rule is `fact25-plan25-v6-author-share-hierarchy`; source snapshot, uploaded rate hash and scope rules remain recorded in the run.

Tests execute the generated exact and broad allocation SQL and the real ordered scope predicate/plan amount expression in SQLite (adapting only APPLY into a correlated scalar join). Cover exact precedence, each fallback level, original subdivision isolation, plan sum preservation, author net returns, zero sums, ineligible/other-year/other-kind exclusion and a branch with no authors. Excel tests preserve the unallocated plan and zero-premium explanation. Actual SQL Server execution must be checked after deployment.

Update the portal and rerun the saved 2025 job; no re-upload or prepared-sales rebuild is required.
