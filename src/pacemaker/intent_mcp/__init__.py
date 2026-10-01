"""
declare_intent MCP server package (Story #155).

Run as ``python -m pacemaker.intent_mcp`` (stdio transport). Modules:
- ``server``       -- the stdlib-only JSON-RPC server (pure acknowledger)
- ``registration`` -- idempotent user-scope ``claude mcp add/remove`` helper
                      used by install.sh
"""
