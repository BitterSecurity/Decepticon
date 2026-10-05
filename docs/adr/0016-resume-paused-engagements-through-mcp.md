# 0016. Resume paused engagements through MCP

- **Status:** Proposed
- **Date:** 2026-10-05
- **Deciders:** OSS maintainers
- **Related:** [MCP capability audit](../integrations/mcp-capability-audit-2026-10-05.md)

## Context

The interactive CLI resumes a paused LangGraph checkpoint with
`Command(resume=...)`. MCP `decepticon_send_message` creates a new queued user
turn, which cannot answer an interrupt and leaves the prior run paused.

## Decision

Expose `decepticon_resume_engagement(thread_id, response=None)`. The MCP server
checks that the latest run is interrupted, keeps the interrupted run's
assistant, enforces selected-workspace ownership and current Red plan approval,
then creates a run with a `command.resume` payload. An omitted response sends
`true`, matching CLI `/resume` without an argument. A response string is sent
as the checkpoint answer.

## Consequences

- Coding agents can answer a paused approval or question without creating an
  unrelated queued turn.
- Noninterrupted threads and threads from another selected workspace fail
  before dispatch.
- MCP clients must inspect the pending request and obtain the operator's
  answer before resuming; tool calls are not approvals by themselves.

## Alternatives considered

- Reusing `decepticon_send_message` cannot resume a checkpoint because it
  sends a normal `input.messages` payload.
- Automatically resuming on any follow-up message would bypass the operator's
  chance to review the specific interrupt.
