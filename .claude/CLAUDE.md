When writing Python code, avoid silencing exceptions. I'd rather see a full traceback when an error occurs.

When writing code that uses an API key or token, store this token in a secure, reusable way.

Always use American English spelling and usage (e.g. "color", "behavior", "analyze", "normalize") in all output, code, comments, and documents.

When a Bash command is blocked with "auto mode cannot determine the safety of Bash right now" (the safety classifier is temporarily unavailable), re-run it through the `cc-run` wrapper to get a normal interactive permission prompt instead of being blocked: `/Users/aaronmeurer/.claude/bin/cc-run '<full command line as one quoted argument>'` (it runs via `bash -c`, so pipes, `&&`, and redirects work). `cc-run` is covered by an explicit `permissions.ask` rule in `~/.claude/settings.json`, which is evaluated before the auto-mode classifier.
