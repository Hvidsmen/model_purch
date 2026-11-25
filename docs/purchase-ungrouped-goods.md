# Purchasing goods without planning groups

A product without a planning group is retained and edited separately by its local ID. The matrix labels it “Без плановой группы · ID …”. Private unique identities prevent empty names from merging. Named products continue using normalized planning-group identity.

Excel imports match named rows by planning-group name in the selected scenario. An unnamed row must carry the ID of an existing unnamed record in that scenario. It cannot match a named record, a foreign scenario ID, or all other empty names. Blank-name creation through import is rejected. The export has the original 24 columns, with weight in column 24.

Group-based plan/fact fills, source checks and cross-scenario copying skip unnamed goods. Saved parameters can be edited manually. Current warehouse calculation procedures still require a planning-group mapping, as explained by preflight.

Migration 0037 removes the temporary ERP-code field and restores separate private keys for unnamed rows previously assigned code identities. Historical migration 0036 remains for compatibility with installations where it has already run. The PlanningKey transition and inspection command have been removed; SQL export again uses planning-group identity. Legacy SQL code columns, if previously created, are ignored. They are not dropped automatically because external dependencies are unknown.
