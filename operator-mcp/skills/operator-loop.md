# Operator Loop Skill

**Pattern:** Worker/verifier iterative cycles.

Run a worker agent, verify the output, repeat until the acceptance criteria are met or max iterations reached.

## When to Use
- Babysitting a PR through CI: worker fixes, verifier runs tests
- Implementation with cross-provider review: Codex codes, Claude verifies
- Iterative refinement: each pass improves on the previous

## Loop Structure

```
1. Create coordination room
2. Spawn worker agent with task
3. Wait for worker to finish
4. Run verification (shell command and/or verifier agent)
5. If pass → done. If fail → post feedback to room, send worker new prompt
6. Repeat until pass or max iterations
```

## Implementation

### Setup
```
chat_create(name="loop-<task>", purpose="Iterative: <description>")
```

### Worker Phase
```
create_agent(
    title="worker-<task>",
    agent_type="codex",  # or claude
    cwd="...",
    initial_prompt="<task with acceptance criteria>"
)
wait_for_agent(agent_id=worker_id)
```

### Verification Phase

**Option A — Shell verification** (fast, deterministic):
Run tests, linters, type checks directly. Parse exit code.

**Option B — Agent verification** (nuanced, catches logic errors):
```
create_agent(
    title="verifier-<task>",
    agent_type="claude",  # different provider than worker
    cwd="...",
    initial_prompt="Review the changes made by <worker>. Check: <criteria>. Report PASS or FAIL with specifics."
)
wait_for_agent(agent_id=verifier_id)
```

**Option C — Both** (recommended for important work):
Run shell checks first. If they pass, run agent verification.

### Iteration
If verification fails:
1. Post failure details to the coordination room
2. Send worker a follow-up with specific feedback:
```
send_agent_prompt(
    agent_id=worker_id,
    prompt="Verification failed: <details>. Fix these issues."
)
wait_for_agent(agent_id=worker_id)
```
3. Re-verify

### Termination
- **Pass**: Post success to room, record outcome as "success"
- **Max iterations**: Post summary of remaining issues, record as "partial"
- **Unrecoverable error**: Record as "failed", escalate to user

## Guidelines

- **Max 5 iterations** by default. If not converging after 3, reconsider the approach.
- **Cross-provider review**: If worker is Codex, verifier should be Claude (and vice versa).
- **Archive workers between iterations** if context is getting stale — fresh agents with targeted prompts often perform better than long-running ones.
- **Trust the wait.** Don't poll. Use `wait_for_agent`.
