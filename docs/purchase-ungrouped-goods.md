# Purchasing goods without planning groups

A product without a planning group is retained and can be edited by its local ID. The goods list labels it “Без плановой группы · ID …” and offers a separate optional ERP source-code field. Migration 0036 adds that field, permits empty group names, and assigns separate stable keys to existing unnamed records without deleting their data. Newly created unnamed records have distinct private keys, or a namespaced ERP-code key if a code is provided. Named planning-group identity and its uniqueness rules remain unchanged.

Named Excel rows continue matching by normalized group name in the selected scenario, ignoring workbook IDs. Unnamed rows require either a matching ERP code in column 25 or the ID of an existing unnamed record of the selected scenario. They never match a named record, a foreign scenario ID, or all other empty names. Creation of an unnamed import requires an ERP code. A provided code that conflicts with an existing ID's code is rejected. ERP codes round-trip through Excel; old 24-column files remain supported.

Current group-level loaders do not retain the original product code. No code is invented for historical records. The group-level refresh skips new empty-name source rows because it cannot distinguish individual ERP products. Users may record a known ERP code explicitly. Group-based plan/fact fills, source checks, and cross-scenario copies skip unnamed goods rather than joining on an empty name. Existing loaded plan fields are retained for such records.

Saving individual parameters and calculating the unit price are supported. The warehouse purchasing procedures currently join by planning group, not the new source code. Preflight therefore explains that an unnamed record cannot yet be used in the MS SQL scenario calculation; adding a source code alone does not enable an unimplemented ERP-code join. This avoids silently merging unrelated products in warehouse results.

Checks cover separate unnamed records, inline edits, local-ID import, ERP-code matching, foreign/named ID rejection, group-fill/copy skips and normalization of historical keys.
