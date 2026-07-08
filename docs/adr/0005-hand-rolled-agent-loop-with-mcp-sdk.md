# ADR 0005: Hand-rolled agent loop with the official MCP SDK

## Status

Accepted

## Context

Agent frameworks (Strands Agents, LangChain, etc.) ship a ready-made
tool-use loop with MCP support. Using one would be faster, but the loop is
exactly where Agora's guarantees live: evidence quotas, iteration caps,
metrics capture, and research-note extraction all hook into it. A
framework hides that layer; this project exists to demonstrate it.

## Decision

Implement the tool-use loop by hand: provider call → inspect tool-use
blocks → enforce quota → execute via MCP client session → append tool
results → repeat until text or iteration cap. Use the official `mcp`
Python SDK as the protocol client (stdio transport to both servers).
No agent framework.

## Consequences

- Full control over enforcement, metrics, and memory capture inside the
  loop; nothing is hidden.
- More code to own than a framework integration.
- The MCP protocol handling itself is NOT hand-rolled — the official SDK
  handles the wire format, so we demonstrate the protocol without
  reimplementing it.
