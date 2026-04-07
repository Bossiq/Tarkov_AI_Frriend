---
description: Systematic debugging workflow — reproduce, isolate, hypothesize, fix, verify
---

# Debug Workflow

When the user reports a bug or asks to debug (or uses /debug):

## Step 1: Reproduce
- Read the relevant source code
- Identify the exact function/method/line where the issue manifests
- If a stack trace was provided, trace it backwards to the root cause

## Step 2: Isolate
- Identify which module owns the bug (brain.py? voice_input.py? voice_output.py? etc.)
- Check if the issue is in the module itself or in how it's called from main.py
- Check threading: is this a race condition? Check all Lock/Event usage.
- Read CLAUDE.md for known gotchas that might be relevant

## Step 3: Hypothesize
- State top 3 hypotheses for what's causing the issue, ranked by likelihood
- For each hypothesis, explain what evidence would confirm or reject it

## Step 4: Verify
- Read specific code sections that confirm/reject each hypothesis
- Run diagnostic commands if needed
- Narrow down to the root cause

## Step 5: Fix
- Implement the minimal fix that resolves the root cause
- Follow all project conventions (see CLAUDE.md)
- Add a comment explaining WHY the fix works

## Step 6: Validate
// turbo
- Run: `python -m pytest tests/test_units.py -v --tb=short`
- If applicable, write a new test that would have caught this bug
- Confirm no regressions
