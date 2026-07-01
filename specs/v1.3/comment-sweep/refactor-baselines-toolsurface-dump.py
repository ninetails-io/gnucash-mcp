"""Dump the full MCP tool surface: sorted (name, description, input schema)."""
import json, sys, os
os.environ.setdefault("GNUCASH_BOOK_PATH", os.path.abspath("samples/alex-chen-morales.gnucash"))
os.environ["GNUCASH_MCP_NOAUDIT"] = "1"
sys.argv = ["gnucash-mcp"]
from gnucash_mcp import server
server._validate_module_groups()
server._validate_tool_modules()
server._apply_module_filter("all")
tools = server.mcp._tool_manager._tools
out = []
for name in sorted(tools):
    t = tools[name]
    schema = t.parameters if hasattr(t, "parameters") else None
    out.append(f"=== {name} ===")
    out.append(t.description or "")
    out.append(json.dumps(schema, indent=1, sort_keys=True, ensure_ascii=False))
print("\n".join(out))
