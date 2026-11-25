# Purchasing goods without planning groups

A product without a planning group is retained and edited separately by its local ID. The planning-group cell stays empty, without a generated name; its local ID is shown in the ID column. Private unique identities prevent empty names from merging. Named products continue using normalized planning-group identity.

Excel imports match named rows by planning-group name in the selected scenario. An unnamed row must carry the ID of an existing unnamed record in that scenario. It cannot match a named record, a foreign scenario ID, or all other empty names. Blank-name creation through import is rejected. The export has the original 24 columns, with weight in column 24.

Group-based source loading, physical-parameter lookups, source checks and cross-scenario copying skip unnamed goods. EXW reverse calculation may use a positive unit DDP already stored on that individual record, without matching a group name. Saved parameters can be edited manually. An empty group alone does not block portal calculation preflight. Export sends the stored empty value without inventing a label or replacing it with an ERP code. Warehouse procedure definitions are unchanged.

Migration 0037 removes the temporary ERP-code field and restores separate private keys for unnamed rows previously assigned code identities. Historical migration 0036 remains for compatibility with installations where it has already run. The PlanningKey transition and inspection command have been removed; SQL export again uses planning-group identity. Legacy SQL code columns, if previously created, are ignored. They are not dropped automatically because external dependencies are unknown.
