# Iteration 2: Design and Hypothesis

## Motivation
Iteration 1 produced two candidates that heavily regressed from the seed baseline (62.5% → 45.8% and 41.7%). Both added complex prompt structures without supporting mechanisms, resulting in:
- Truncated outputs exceeding reasonable length
- Missing ANSWER lines and extraction failures  
- Higher token costs (+20-23%)

**Key lesson**: Complexity without targeted support hurts more than it helps.

## Two New Mechanisms

### Mechanism 1: Error Recovery (cand_error_recovery)
**Type**: Exploitation (improve seed with minimal changes)  
**Axes**: E (learning trigger), A (recovery prompt)

**Hypothesis**: Many answer extraction failures come from formatting issues, not reasoning failures. A recovery mechanism that explicitly requests reformatted output can catch these cases without adding complexity to the main reasoning.

**How it works**:
1. First attempt: same as seed (minimal prompt)
2. If ANSWER line not found, trigger recovery:
   - Second call asking model to explicitly format the answer
   - Extract final answer from recovery attempt
3. Fallback: use last non-empty line if still no answer

**Cost**: ~1 additional model call when extraction fails (selective)

**Expected benefit**: Better accuracy on problems where reasoning is correct but formatting is poor.

---

### Mechanism 2: Code Solver (cand_code_solver)
**Type**: Exploration (different approach)  
**Axes**: C (problem detection), F (code generation and tool execution)

**Hypothesis**: Structured reasoning problems (tracking objects, evaluating boolean expressions, counting patterns) are fundamentally more reliable to solve through explicit code than text-based reasoning. Code generation and execution reduces calculation errors and improves accuracy on these problem families.

**How it works**:
1. Detect problem structure via keyword/operator analysis
   - Tracking indicators: dance, switch, partner, position
   - Counting indicators: count, how many, sum
   - Boolean indicators: true, false, expressions
   - Math indicators: numbers + operators (max, min, <=, etc.)

2. For structured problems:
   - Ask model to generate Python code to solve
   - Execute code via tools.python
   - Extract answer from code output

3. For unstructured or if code fails:
   - Fall back to text reasoning (like seed)

**Cost**: 
- ~1 additional model call for code generation
- ~1 tool execution call
- Only for detected structured problems

**Expected benefit**: Higher accuracy on tracking (shuffled_objects) and boolean problems, which are seed's weak points.

---

## Design Principles Applied

1. **Avoid verbose prompts**: Learned from iter1 regression
2. **Targeted mechanisms**: Not one-size-fits-all guidance
3. **Fallback safety**: Always revert to text if code fails
4. **Selective costs**: Only add calls when mechanisms activate
5. **Mix strategies**: One exploitation, one exploration
6. **Generalization**: No dataset-specific hints or hard-coded answers

---

## Expected Outcomes

| Mechanism | Expected Win | Risk | Cost |
|-----------|--------------|------|------|
| Error Recovery | Fixes formatting failures | Minimal (adds one call) | Low |
| Code Solver | Improves tracking/boolean | May generate invalid code | Medium |
| Baseline | Consistent baseline | - | Very Low |

Both candidates avoid the complexity overload that caused iter1's regression by staying lean and selective.
