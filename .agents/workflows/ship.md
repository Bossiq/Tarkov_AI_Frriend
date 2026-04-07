---
description: Stage, commit with conventional commit message, and prepare for push
---

# Ship Workflow

When the user asks to ship/commit (or uses /ship):

// turbo
1. Check status: `git status`

// turbo
2. See changes: `git diff --stat`

// turbo
3. Run tests first — ALL must pass before committing:
```
python -m pytest tests/test_units.py -v --tb=short
```

4. If tests fail, fix them first (follow /test workflow)

5. Stage appropriate files with `git add` (NEVER blindly `git add .`)
   - Skip: .env, logs/, __pycache__/, *.pyc, models/*.onnx

6. Generate a conventional commit message:
   - `feat:` for new features
   - `fix:` for bug fixes
   - `refactor:` for code restructuring
   - `docs:` for documentation changes
   - `test:` for test additions/changes
   - `chore:` for maintenance tasks

7. Commit: `git commit -m "type: concise description"`

// turbo
8. Confirm: `git log -1 --stat`

CRITICAL: NEVER force push. NEVER commit .env or API keys. NEVER commit binary model files.
