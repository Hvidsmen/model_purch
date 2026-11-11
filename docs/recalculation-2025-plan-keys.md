# Canonical 2025 plan and fact classification

Use the existing classification consistently on both sides before file-rate matching:

- Goods group is used for `1. RAC`, `2. VRF`, `3. PROF`; otherwise empty.
- Brand is used only for household conditioners, semi-industrial conditioners, multi-split systems and VRF systems. All other groups, including mobile conditioners, have an empty brand even if Product/Goods has a manufacturer brand.

The plan's inner SELECT/GROUP BY retains this original classification. Fact rate lookup now uses the same expressions instead of raw Product brand/group. Empty classified dimensions stay empty in the final rate projection, including when a matching file row contains `*` or a minimum is used. This makes author-plan-share keys agree without introducing brand splits for mobile goods. File exact/wildcard/minimum priority, monetary input totals, original subdivisions and payout rules remain unchanged. Audit rule: `fact25-plan25-v5-canonical-classification`.

The prior raw-key change in rule v4 was incorrect for this business classification and is superseded. Do not treat the old empty mobile brand as missing input data.

Plan validation applies only to participating rows: existing named subdivision exclusions and HEAT/DPP policy exclusions cannot trigger a missing-author guard before they are later excluded from premium calculation. Sales HEAT/DPP remain participating. No genuinely missing author allocation or missing rate is silently accepted. Error 50025 distinguishes the cause and provides the first affected subdivision, planning group, goods group, brand, kind, plan amount and total affected row count in private SQL Agent history.

Tests execute both actual plan input queries in SQLite and verify that mobile brands merge into the empty brand independently per subdivision, while applicable brands remain. Generated fact/plan selectors are executed for exact, wildcard and minimum cases; tests verify that a manufacturer-specific mobile row does not override the brandless wildcard rule and that fallback also leaves the mobile brand empty. Validation exclusions and real missing-author/rate errors remain covered. Actual SQL Server execution must be checked after deployment.

After updating, relaunch the saved 2025 job; uploaded coefficients and shared prepared sales remain usable.
