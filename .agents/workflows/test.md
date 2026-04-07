---
description: Run the full test suite, analyze failures, and fix them
---

# Test Workflow

When the user asks to run tests (or uses /test):

// turbo
1. Run unit tests:
```
python -m pytest tests/test_units.py -v --tb=short
```

2. If ALL tests pass → report summary (total/passed/failed/time) and exit

3. If ANY test fails:
   a. Read the failing test and the source code it tests
   b. Determine if the bug is in the test or the source code
   c. Fix the bug (prefer fixing source over weakening tests)
   // turbo
   d. Re-run tests to confirm the fix
   e. Repeat until all tests pass

4. Report:
   - Total tests run / passed / failed / skipped
   - Time elapsed
   - Any files modified (with rationale)

// turbo-all
