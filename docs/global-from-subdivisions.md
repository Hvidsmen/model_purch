# Global rates from subdivision rates

In the global coefficient action hub, the Iteration row opens “Собрать глобальные из подразделений”. Only motivation editors can access the operation. Applying is allowed only to the latest draft version; create a new draft first when a separate coefficient iteration is required.

Select a loaded sales plan and any of Policies, Sales, Directors and Curators. Each goods classification and K0–K4 cell is weighted by the participating USD plan base of its corresponding O0–O4 segment. Global-only plan rows are ignored to avoid double counting. Group, channel and subdivision exclusions use the shared calculation rules. Director/curator rates are summed across active, currently assigned recipients before weighting; curator branches remain physical. The manager multiplier is not included in rates.

Preview lists the old/new percentages, denominator and branch contributions. Rates round to six fractional decimal places (four in percentage display). A zero denominator preserves the current rate. Negative weights, ambiguous goods, unmatched plan classifications, missing exact Policies/Sales rates, partial employee matrices or absent global target settings block application. No fallback rate or formula is invented. Curator plans combining multiple source branches in one row require reloading a physical branch plan.

The signed preview is bound to the editor, selected version/revision, plan and groups and expires after 30 minutes. Application locks the editable version and plan, recomputes the complete result, and checks its digest before changing anything. Only global rate values change; formula settings and local rates are retained. The whole operation is atomic and logged in CoefficientGroupCopyLog with the plan and branch contributions. No DWH calculation or coefficient approval runs automatically.

Verification: test_global_from_branches, plus comparison, exclusion, group-copy and plan-summary regressions. No migration is needed.
