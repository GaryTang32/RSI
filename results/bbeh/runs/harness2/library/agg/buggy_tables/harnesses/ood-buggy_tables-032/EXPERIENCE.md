# ood-buggy_tables-032

- adopted: `agg1`  (harness `c9ff3d4dd5`)
- components: 3 files (1 code, 2 text); 83 text chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: solve() function
candidates: inv1, dom1, agg1
change: All three candidates removed the llm() call and replaced it with deterministic table parsing. inv1 calls a parse_and_solve_table() function directly; dom1 and agg1 use tools.python() to execute an inline Python code string. Both approaches avoid the LLM's hardcoded value lists seen in base1.
observed: base1 delivered 396.50 using hardcoded sleep and study arrays (22 and 25 elements, mismatched lengths, never parsed from input); inv1 delivered 396.5; dom1 and agg1 delivered 395.50. The shift away from LLM is clear, but outputs diverge.
verdict: confounded

component: filtering condition for null values in sleep_minutes
candidates: inv1 vs dom1/agg1
change: inv1 skips entire rows where sleep_minutes is null (line in parse_and_solve_table: `if study_str == 'null' or calories_str == 'null' or sleep_str == 'null': continue`). Dom1/agg1 skip rows only when filtering condition fields are null (`if study_minutes == "null" or calories_consumed == "null": continue`), then separately collect non-null sleep and study values, so rows passing the filter contribute their non-null sleep to sleep_vals even if sleep is null (skipped from that median) and non-null study to study_vals.
observed: inv1 produced 396.5; dom1/agg1 produced 395.50 (difference of 0.95). The adoption decision analysis (in context/decisions/agg1.md) identifies inv1's filtering as "overly restrictive" and explicitly states this error "propagates through to the final median calculation and produces an incorrect answer (396.5)."
verdict: helped (dom1/agg1's filtering logic is correct per task spec: "If a value is null, ignore it from the computations" means ignore null values *from their respective median*, not skip entire rows; inv1's violation of this logic causes it to exclude rows that should contribute non-null study values)

## Transferable lesson
- **cue**: When a harness must filter rows by multiple conditions and handle missing values in non-filtering fields, the design must separate two concerns: which rows pass the filter (determined only by filtering-condition fields), and which values contribute to each aggregation (determined by per-field nullness).
- **lesson**: In filtering/aggregation harnesses, implement the filter condition (lines that `continue` or skip rows) to check only the fields that define the filter. For any non-filtering field that is null on a row that passes the filter, skip that value *from its aggregation* (e.g., do not append to sleep_vals), but do not skip the row entirely. This ensures all passing rows contribute their non-null values to their respective aggregations, matching the intent of "ignore null values from computations" (ignore from the computation they are null in, not from the row's inclusion in the filter).
- **evidence**: Edit row 2 (filtering condition) shows inv1 conflates row filtering with value collection by skipping rows with null sleep_minutes, while dom1/agg1 correctly decouple them. The 0.95-point difference in output (396.5 vs 395.50) and the explicit analysis in context/decisions/agg1.md confirm that inv1's error propagates through to the final result. Adoption chose dom1/agg1 (395.50) as credible; the decision cites inv1's filtering error as making its output "unreliable."
- **confidence**: high. The error is observable in code inspection (the conditional differs between candidates), the execution contrast is clear (396.5 vs 395.50), and the adoption reasoning explicitly identifies this as the deciding factor between otherwise equivalent approaches (both move away from LLM, both parse the table, only filtering logic differs).
