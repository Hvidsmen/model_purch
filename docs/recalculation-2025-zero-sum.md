# Zero-sum policy plan allocation in 2025

Annual policy plan shares stay partitioned by original document subdivision, planning group, coefficient goods group and coefficient brand. Moscow/TT and other original subdivisions never supply each other's weights. Only Policies and fact year 2025 participate.

Aggregate signed fact per author first. If the combination's signed total is nonzero, keep the previous signed fact / signed total rule. If its total is exactly zero, use max(author net fact, 0) / sum(max(author net fact, 0)) within the same partition. Thus author nets +100, +200, -300 receive 1/3, 2/3, 0. Author returns are netted before weights; the turnover, return amounts and premium monetary base are not changed. Zero-net authors receive no plan. When every author net is zero there is no positive denominator; a nonzero plan without fact for distribution remains an explicit error rather than getting arbitrary equal shares.

The rule is recorded as `fact25-plan25-v3-zero-sum-positive-shares`, with a dedicated `zero_sum_policy_plan_shares` audit parameter. File minimum fallback and all payout rules remain unchanged. Re-launch the saved 2025 job after updating the portal; re-upload is unnecessary.

For a failed 2025 run the report now shows the last five dated SQL Agent job history entries to users who can launch calculations. History belongs to the exact selected job; timestamps are Moscow time and are displayed for correlation with the audited run. Messages are escaped text. If Agent history is unavailable/cleared, the page says so and provides the exact job name for SSMS. It does not substitute an old result or export after a known failed/interrupted attempt. Users without launch rights do not receive raw SQL job output.

Tests replay the actual allocation SELECT in SQLite and cover zero-sum partitions, author net vs gross positive invoices, subdivision/goods/brand/year/kind isolation, unchanged signed nonzero totals and absence of invented equal shares. Page tests cover dated errors, HTML escaping, unavailable logs, restricted viewers and refusal to export stale output. Real SQL Server execution must be checked after deployment.
