#!/usr/bin/env bash
# Register the one universal Agency MCP server (mcp/server.py) with any
# MCP-capable tool. CLI-based tools are registered directly; file-based tools
# get the standard mcpServers block merged into their config IF the tool's
# config dir exists (otherwise the snippet is printed to paste later).
#
# Usage:
#   ./mcp/register.sh                 # register every tool detected on this machine
#   ./mcp/register.sh claude codex    # register only the named tools
set -euo pipefail

SERVER="$(cd "$(dirname "$0")" && pwd)/server.py"
PY="$(command -v python3)"
NAME="agency-agents"

json_block() { cat <<JSON
{
  "mcpServers": {
    "$NAME": { "command": "$PY", "args": ["$SERVER"] }
  }
}
JSON
}

# Merge the mcpServers.$NAME entry into a JSON config file (creates it if absent).
merge_json() { # $1 = config path, $2 = top-level key (mcpServers | mcp)
  local file="$1" key="${2:-mcpServers}"
  [ -d "$(dirname "$file")" ] || { echo "  skip: $(dirname "$file") not found — paste this into $file:"; json_block; return; }
  "$PY" - "$file" "$key" "$NAME" "$PY" "$SERVER" <<'PY'
import json, sys, os
file, key, name, py, server = sys.argv[1:6]
data = {}
if os.path.exists(file):
    try: data = json.load(open(file, encoding="utf-8"))
    except Exception: data = {}
data.setdefault(key, {})[name] = {"command": py, "args": [server]}
json.dump(data, open(file, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print(f"  merged {name} into {file} (key: {key})")
PY
}

register() {
  echo "==> $1"
  case "$1" in
    claude)   claude mcp add --scope user "$NAME" -- "$PY" "$SERVER" 2>&1 | sed 's/^/  /' || true ;;
    codex)    codex mcp add "$NAME" -- "$PY" "$SERVER" 2>&1 | sed 's/^/  /' || true ;;
    gemini)   merge_json "$HOME/.gemini/settings.json" mcpServers ;;
    cursor)   merge_json "$HOME/.cursor/mcp.json" mcpServers ;;
    opencode) merge_json "$HOME/.config/opencode/opencode.json" mcp ;;
    copilot)  merge_json "$HOME/.copilot/mcp-config.json" mcpServers ;;
    windsurf) merge_json "$HOME/.codeium/windsurf/mcp_config.json" mcpServers ;;
    kimi)     merge_json "$HOME/.config/kimi/mcp.json" mcpServers ;;
    qwen)     merge_json "$HOME/.qwen/settings.json" mcpServers ;;
    vibe)     merge_json "$HOME/.vibe/mcp.json" mcpServers ;;
    zcode)    merge_json "$HOME/.zcode/mcp.json" mcpServers ;;
    # Rules/skill-file tools: NOT MCP tool-callers. Use the repo converter instead.
    aider|windsurf-rules|osaurus|openclaw|hermes|antigravity)
      echo "  not an MCP client — use: ./scripts/convert.sh --tool $1 && ./scripts/install.sh --tool $1" ;;
    *) echo "  unknown tool: $1" ;;
  esac
}

TOOLS=("$@")
[ ${#TOOLS[@]} -eq 0 ] && TOOLS=(claude codex gemini cursor opencode copilot windsurf kimi qwen vibe zcode)
for t in "${TOOLS[@]}"; do register "$t"; done
echo "Done. Restart each tool / start a new session so it picks up the server."
