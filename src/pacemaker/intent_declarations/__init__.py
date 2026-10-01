"""
Tool-first intent declaration (Story #155): the declaration store plus the
hook-side recorder/resolver that let Write/Edit intent come from the
``declare_intent`` MCP tool before the transcript is consulted.

Modules:
- ``fields``  -- stdlib-only shared field rules (also used by the MCP server)
- ``store``   -- SQLite store (declarations + chains, 60-minute TTL)
- ``gate``    -- PostToolUse recorder and PreToolUse resolver wiring helpers
"""
