---
description: Refactor a module while maintaining all tests and correctness
---

# Refactor Workflow

When the user asks to refactor (or uses /refactor):

## Pre-flight
// turbo
1. Run existing tests to establish baseline:
```
python -m pytest tests/test_units.py -v --tb=short
```

2. All tests MUST pass before starting. If not, fix first.

## Rules
- **No behavior change**: Public API signatures and observable behavior must not change
- **Incremental**: Small, verifiable changes. Test after each step.
- **Document**: Update docstrings if internal architecture changes
- **Conventions**: Follow all patterns from CLAUDE.md

## Checklist
- [ ] Extract long functions (>50 lines) into focused helper methods
- [ ] Replace magic numbers with named constants
- [ ] Consolidate duplicate code into shared utilities
- [ ] Simplify complex conditionals (max 3 levels of nesting)
- [ ] Ensure all public methods have type hints and docstrings
- [ ] Remove dead code (unreachable branches, unused imports, commented-out blocks)
- [ ] Check error handling (no bare except)
- [ ] Verify thread safety (Lock/Event usage for shared mutable state)

## Post-flight
// turbo
3. Run tests again to confirm no regressions:
```
python -m pytest tests/test_units.py -v --tb=short
```

4. Report:
   - Summary of changes made
   - Test results (before and after)
   - Lines of code before vs after
   - Follow-up suggestions out of scope
