---
description: Full code review of a file or recent changes for production-readiness
---

# Code Review Workflow

When the user asks to review code (or uses /review):

1. Read the target file(s) completely
2. Read CLAUDE.md for project conventions and critical rules
3. Perform a thorough review covering:
   - **Correctness** — Logic errors, edge cases, off-by-one, race conditions
   - **Thread Safety** — Shared mutable state must use threading.Lock/Event
   - **Error Handling** — No bare except, non-fatal logged+continued, fatal to crash.log
   - **Conventions** — Logging (getLogger), naming (_UPPER_SNAKE constants), type hints, docstrings
   - **Security** — No hardcoded secrets, no unsafe eval/exec
   - **Performance** — No blocking I/O on main thread, no unnecessary hot-path allocations
   - **API Budget** — Gemini fits 1500 RPD, Groq handles rate limits

4. For each issue, output:
   - **Severity**: CRITICAL / WARNING / STYLE
   - **Location**: file:line
   - **Issue**: what's wrong
   - **Fix**: exact code change

5. If no issues found, confirm the code is production-ready.

// turbo-all
