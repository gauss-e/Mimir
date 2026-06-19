"""Infrastructure layer: provider-agnostic plumbing the app builds on.

Houses the cross-cutting technical modules that aren't domain logic:

- ``llm``  — provider-agnostic LLM client (openai / anthropic / ollama).
- ``mcp``  — MCP client (registry + live hub) for calling external tools.

Kept under one top-level ``infrastructure`` package so domain modules
(``profiles`` and future career-advisor logic) import from a stable place.
"""
