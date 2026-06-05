# Construct Workflows Skill

Use this skill when designing, composing, editing, or executing automated agent workflows, pipeline execution structures, and task DAGs in Revka.

## Purpose

To ensure that workflows are constructed with high stability, clear state boundaries, explicit B2B governance, complete auditability, and robust error recovery.

## Workflow Construction Principles

1. **Explicit DAG Definition**:
   - Every workflow must be structured as a directed acyclic graph (DAG) or a clearly sequenced chain of steps.
   - Declare precise inputs and outputs for each step.
   - Avoid hidden or dynamic dependencies that cannot be validated pre-execution.

2. **Role-Driven Specialization**:
   - Map workflow steps to specialized agents or services.
   - Do not use a single giant agent to handle diverse concerns. Delegate to focused subagents (e.g., triage, release engineering, security auditing).

3. **Governance and Approval Boundaries**:
   - Design workflows to pause and request human-in-the-loop or external system approvals before executing high-risk, mutating operations (such as actual production rollback, code deployment, or major configuration edits).
   - Require structured validation of preconditions before entering approval gates.

4. **Comprehensive Evidence Gathering**:
   - Incorporate explicit steps for fetching, validating, and logging system evidence (such as Cloud Run descriptors, Cloud Logging traces, or deployment diffs) at key stages.
   - All collected evidence should be formatted as structured JSON or markdown and stored securely in the execution context.

5. **Built-in Compensation and Rollback**:
   - Every mutating step in a workflow must have a defined rollback or recovery counter-action.
   - Workflows must return complete compensation plans in case of partial failures or timeout.

6. **Kumiho Memory Integration**:
   - Workflow runs, task structures, and execution history should be persisted in Kumiho's Space and Item revision graph to ensure long-term auditability and provenance.
