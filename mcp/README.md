# Agency Agents — Universal Lazy Router (MCP)

One zero-dependency MCP server (`server.py`) exposes the full ~265-agent Agency
roster as **4 tools** to any MCP-capable client, instead of loading every
agent's name+description into the model's context (the ~15k-token roster bloat).

```
265 agents on disk  ──►  server.py (search/inspect/load/delegate)  ──►  any MCP client
```

## Tools

| Tool | Purpose |
| --- | --- |
| `search_agents(query, division?, limit?)` | Rank specialists by capability. Returns slugs. |
| `inspect_agent(slug, include_body?)` | One specialist's metadata (or full body). |
| `load_agent(slug, task?)` | Specialist prompt, ready to adopt **inline**. |
| `delegate_agent(slug, task)` | Delegation packet to run in an **isolated subagent**. |

`delegate_agent` returns a prompt, not a running agent — MCP cannot spawn a
host subagent. The host runs it natively (Claude Code: `Task` tool with
`subagent_type="general-purpose"`), which is what gives real isolation +
parallelism.

## Verify the server

```bash
python3 mcp/server.py --selftest     # asserts parse + search + load + delegate
```

## Register with a tool

CLI-based tools are registered automatically; file-based tools get the standard
`mcpServers` block merged in:

```bash
./mcp/register.sh              # every tool detected on this machine
./mcp/register.sh cursor       # just one
```

### MCP-capable tools (use `server.py` directly)

| Tool | How | Config |
| --- | --- | --- |
| Claude Code | `claude mcp add --scope user agency-agents -- python3 <repo>/mcp/server.py` | `~/.claude.json` |
| Codex | `codex mcp add agency-agents -- python3 <repo>/mcp/server.py` | `~/.codex/config.toml` |
| Gemini CLI | `register.sh gemini` | `~/.gemini/settings.json` → `mcpServers` |
| Cursor | `register.sh cursor` | `~/.cursor/mcp.json` → `mcpServers` |
| opencode | `register.sh opencode` | `~/.config/opencode/opencode.json` → `mcp` |
| Copilot CLI | `register.sh copilot` | `~/.copilot/mcp-config.json` → `mcpServers` |
| Windsurf | `register.sh windsurf` | `~/.codeium/windsurf/mcp_config.json` → `mcpServers` |
| Kimi / Qwen / Vibe / ZCode | `register.sh <tool>` | tool's `mcpServers` JSON |

Universal JSON block (paste into any `mcpServers` config):

```json
{
  "mcpServers": {
    "agency-agents": { "command": "python3", "args": ["/home/ttri0/agency-agents/mcp/server.py"] }
  }
}
```

### Not MCP clients — use the repo converter instead

Aider (`CONVENTIONS.md`), Windsurf rules (`.windsurfrules`), Osaurus/Antigravity
(skills), OpenClaw (workspace), and Hermes (plugin) don't call MCP tools. For
these the lazy-router idea maps to the repo's own generators:

```bash
./scripts/convert.sh --tool hermes && ./scripts/install.sh --tool hermes
```

(Hermes already ships an equivalent lazy-router plugin; the others are
roster/skill files — curate what you install rather than installing all 265.)

## Claude Code usage

The roster is no longer preloaded. One native agent (`~/.claude/agents/agency-router.md`)
drives it. Call it in natural language:

- *"Use agency-router: find and use godot-shader-developer to fix this shader."*
- *"Use Agency to audit this smart contract."* (router searches, picks, runs)

The 254 previously-installed agent files were moved to
`~/.claude/agents-backup-agency/` (reversible). Restore with:

```bash
mv ~/.claude/agents-backup-agency/*.md ~/.claude/agents/
```
