# Iteration 0: Seed Baseline

**Performance**: 33.3% accuracy (4/12 tasks), 2237.5 avg tokens

**Correct** (001, 006, 008, 011): Modular arithmetic and one digit-sum where model got computation right.

**Failed** (000, 002-005, 007, 009-010): 
- Digit-sum arithmetic errors: models miscalculate sums (41!, 52^12, 66!, 64!, 25! all off)
- Format issue (002): "-183,764" has commas; expected "-183764"
- Complex modulo (003): wrong CRT calculation despite setup
- Bit counting (010): miscounted 1-bits

**Root causes**: (1) LLM mental arithmetic unreliable for multi-digit operations; (2) format variation in output; (3) Python available in traces but not systematically used for verification.

**Takeaway**: Improvements must force deterministic computation (Python) and extract answers reliably. Next iteration explores two mechanisms: enforcing Python upfront with marked results (exploitation) vs. executing code and extracting from tool output (exploration).
