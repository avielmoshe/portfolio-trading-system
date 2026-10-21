---
description: Run the entire backend test suite via the isolated qa-tester agent
---

Delegate this entire task to the **`qa-tester`** subagent. Do not run the tests yourself, and do
not analyse or fix anything before handing off — the point of this command is an independent
verdict from an agent with no context about what we were trying to build.

Launch it with the Agent tool, `subagent_type: "qa-tester"`, and this instruction:

> Run the entire backend test suite (every file in tests/) and produce your QA REPORT. The command is
> `docker compose exec -T -w /app api pytest tests -v`, run from the
> repository root. Report findings only. Do not modify any code, tests, or configuration.

$ARGUMENTS

If arguments were given above, pass them to the agent as extra scope (for example a specific test
name to run, or `-vv` for more detail) — but the agent must still report rather than fix, and must
not skip or deselect tests.

When the agent returns, relay its QA REPORT to me **verbatim**. Add nothing of your own: no
reassurance, no explanation of why a failure is acceptable, no proposed fixes unless I ask for
them separately. If the report says FAIL, state that plainly in your first line.
