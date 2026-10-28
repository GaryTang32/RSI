Perfect! Iteration 3 is complete. Here's the summary:

## Summary

I've successfully completed **Iteration 3** of the evolution loop with the following deliverables:

### Step 0: Post-Eval Reports ✅
- **`reports/iter1.md`** - Analyzed why fact_memory and step_validate failed (0%)
- **`reports/iter2.md`** - Analyzed why code_required and example_retrieval failed (0%)

**Key Finding**: All previous attempts failed due to unreliable problem-type detection logic that broke fallback paths.

### Step 1-2: Analysis & Hypotheses ✅
Analyzed the evolution history and identified the root cause: iterations 1-2 relied on regex-based detection to categorize problems, but this approach broke the entire system.

### Step 3: Prototyping ✅
Created `test_prompts.py` to prototype and compare different prompt variants on real traces.

### Step 4: Two New Candidates ✅

#### **Candidate 1: enhanced_reasoning** (Axis A - Prompt Template)
- **Hypothesis**: A more detailed system prompt emphasizing step-by-step reasoning, state tracking, and pattern analysis will improve all problems, especially weak areas (linguini 25%, shuffled_objects 25%)
- **Mechanism**: Replaced generic system prompt with guidance-focused instructions
- **Key Principle**: Single LLM call, no detection logic, works on all problem types

#### **Candidate 2: structured_output** (Axis D - Memory/Output Structure)  
- **Hypothesis**: Explicit working space in task prompt before final answer improves accuracy on complex problems
- **Mechanism**: Restructured task prompt to request "Show reasoning, then end with ANSWER:"
- **Key Principle**: Single LLM call, no detection logic, universal benefit

### Files Created:
- ✅ `agents/enhanced_reasoning/` - Complete harness with system prompt
- ✅ `agents/structured_output/` - Complete harness with task prompt structure
- ✅ `pending_eval.json` - Candidate metadata with falsifiable hypotheses
- ✅ `_proposal.json` - Summary of changes and reasoning

### Design Philosophy
Unlike previous failures that used detection logic, iteration 3 focuses on:
- **Universal improvements** (no detection)
- **Single-call simplicity** (like seed)
- **Pure prompt engineering** (no complex logic)
- **Low risk** (backward compatible)

Both candidates aim to improve beyond seed's 62.5% by encouraging better reasoning without the risk of detection logic that broke everything in iterations 1-2.