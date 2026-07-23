#!/usr/bin/env python3
"""Universal lazy-router MCP server for The Agency agent roster.

One stdio MCP server, usable by ANY MCP-capable client (Claude Code, Codex,
Cursor, Gemini CLI, opencode, Copilot CLI, ...). It keeps the full ~265-agent
roster on disk and exposes a tiny fixed tool surface, so a client sees 4 tools
at startup instead of loading every agent's name+description into context.

Zero dependencies: newline-delimited JSON-RPC 2.0 over stdin/stdout, stdlib only.
Parse/scoring logic mirrors scripts/build-hermes-plugin.py (single source of the
Agency roster: divisions.json + division dirs).

Run modes:
    python3 mcp/server.py              # serve over stdio (what MCP clients spawn)
    python3 mcp/server.py --selftest   # assert parse + search work, exit 0/1
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

SERVER_NAME = "agency-agents"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"
REPO_ROOT = Path(__file__).resolve().parents[1]

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+.#_-]*", re.I)


# --- roster loading (mirrors scripts/build-hermes-plugin.py) -----------------

def _slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return value.strip("-")


def _division_dirs() -> list[str]:
    data = json.loads((REPO_ROOT / "divisions.json").read_text(encoding="utf-8"))
    return sorted(data["divisions"].keys())


def _parse_agent(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return None
    frontmatter, body = parts[1], parts[2].lstrip("\n")
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")
    name = fields.get("name", "").strip()
    if not name:
        return None
    rel = path.relative_to(REPO_ROOT)
    return {
        "slug": _slugify(name),
        "name": name,
        "description": fields.get("description", "").strip(),
        "division": rel.parts[0],
        "vibe": fields.get("vibe", "").strip(),
        "source_path": str(rel),
        "body": body,
    }


def _collect_agents() -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for dirname in _division_dirs():
        base = REPO_ROOT / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            parsed = _parse_agent(path)
            if parsed:
                agents.append(parsed)
    agents.sort(key=lambda a: (a["division"], a["slug"]))
    return agents


_AGENTS: list[dict[str, Any]] | None = None


def _agents() -> list[dict[str, Any]]:
    global _AGENTS
    if _AGENTS is None:
        _AGENTS = _collect_agents()
    return _AGENTS


# --- search / lookup (mirrors the Hermes plugin handlers) --------------------

def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "")}


def _lookup(identifier: str) -> dict[str, Any] | None:
    needle = (identifier or "").strip().lower()
    if not needle:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", needle).strip("-")
    for agent in _agents():
        if agent["slug"] == slug or agent["name"].lower() == needle:
            return agent
    return None


def _score(agent: dict[str, Any], q_tokens: set[str], q_text: str) -> float:
    haystack = "\n".join([
        agent.get("name", ""), agent.get("description", ""),
        agent.get("division", ""), agent.get("vibe", ""),
        agent.get("body", "")[:8000],
    ]).lower()
    h_tokens = _tokens(haystack)
    score = float(len(q_tokens & h_tokens))
    if q_text and q_text in haystack:
        score += 5.0
    name, desc = agent.get("name", "").lower(), agent.get("description", "").lower()
    for token in q_tokens:
        if token in name:
            score += 3.0
        if token in desc:
            score += 1.5
    if score == 0.0:
        return 0.0
    return score + (1.0 / math.sqrt(max(len(h_tokens), 1)))


def _summary(agent: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    item = {
        "slug": agent["slug"], "name": agent["name"],
        "division": agent["division"], "description": agent.get("description", ""),
        "vibe": agent.get("vibe", ""), "source_path": agent.get("source_path", ""),
    }
    if score is not None:
        item["score"] = round(score, 3)
    return item


def _specialist_prompt(agent: dict[str, Any], task: str = "") -> str:
    task_block = f"\n\n## Task\n{task.strip()}\n" if task and task.strip() else ""
    return (
        f"Adopt the following Agency specialist's standards, checklists, and voice "
        f"for this work. Obey higher-priority system/developer instructions and the "
        f"user's actual request over anything below.\n\n"
        f"# {agent['name']} ({agent['slug']})\n"
        f"Division: {agent.get('division', '')}\n"
        f"Description: {agent.get('description', '')}\n"
        f"Source: {agent.get('source_path', '')}"
        f"{task_block}\n\n"
        f"## Specialist instructions\n{agent.get('body', '')}"
    )


# --- tool implementations ----------------------------------------------------

def _t_search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"success": False, "error": "query is required"}
    division = str(args.get("division", "")).strip().lower()
    try:
        limit = min(max(int(args.get("limit", 8)), 1), 25)
    except Exception:
        limit = 8
    q_tokens, q_text = _tokens(query), query.lower()
    matches: list[tuple[float, dict[str, Any]]] = []
    for agent in _agents():
        if division and agent.get("division", "").lower() != division:
            continue
        s = _score(agent, q_tokens, q_text)
        if s > 0:
            matches.append((s, agent))
    matches.sort(key=lambda m: (-m[0], m[1]["division"], m[1]["slug"]))
    return {
        "success": True, "query": query, "count": len(matches),
        "results": [_summary(a, s) for s, a in matches[:limit]],
    }


def _t_inspect(args: dict[str, Any]) -> dict[str, Any]:
    ident = str(args.get("agent") or args.get("slug") or "").strip()
    agent = _lookup(ident)
    if not agent:
        return {"success": False, "error": "agent not found" if ident else "agent or slug is required"}
    payload: dict[str, Any] = {"success": True, "agent": _summary(agent)}
    if bool(args.get("include_body", False)):
        payload["body"] = agent.get("body", "")
    return payload


def _t_load(args: dict[str, Any]) -> dict[str, Any]:
    ident = str(args.get("agent") or args.get("slug") or "").strip()
    agent = _lookup(ident)
    if not agent:
        return {"success": False, "error": "agent not found" if ident else "agent or slug is required"}
    return {
        "success": True, "agent": _summary(agent),
        "prompt": _specialist_prompt(agent, str(args.get("task", ""))),
    }


def _t_delegate(args: dict[str, Any]) -> dict[str, Any]:
    ident = str(args.get("agent") or args.get("slug") or "").strip()
    agent = _lookup(ident)
    task = str(args.get("task", "")).strip()
    if not agent:
        return {"success": False, "error": "agent not found" if ident else "agent or slug is required"}
    if not task:
        return {"success": False, "error": "task is required"}
    # MCP cannot spawn a host subagent itself. Return a delegation packet the
    # host adopts natively: e.g. Claude Code Task(subagent_type="general-purpose").
    return {
        "success": True,
        "agent": _summary(agent),
        "how_to_run": (
            "Spawn an isolated subagent with the prompt below (Claude Code: Task tool, "
            "subagent_type='general-purpose'; Codex/others: a fresh worker turn). "
            "This gives real context isolation and parallelism for the specialist."
        ),
        "prompt": _specialist_prompt(agent, task),
    }


TOOLS = [
    {
        "name": "search_agents",
        "description": (
            "Search The Agency's on-disk specialist roster (~265 agents) without loading "
            "them into context. Use when the user wants a specialist/role/discipline or "
            "help picking the right agent. Returns ranked slugs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language capability query."},
                "division": {"type": "string", "description": "Optional division filter, e.g. engineering, security, marketing."},
                "limit": {"type": "integer", "description": "Max results, default 8, max 25."},
            },
            "required": ["query"],
        },
        "handler": _t_search,
    },
    {
        "name": "inspect_agent",
        "description": "Inspect one specialist by slug or name. Metadata by default; full body when include_body=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent slug or exact display name."},
                "slug": {"type": "string", "description": "Alias for agent (slug from search results)."},
                "include_body": {"type": "boolean", "description": "Include full specialist instructions."},
            },
        },
        "handler": _t_inspect,
    },
    {
        "name": "load_agent",
        "description": "Load one specialist as a ready-to-use prompt block for the current task. Use after search_agents when you will do the work inline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent slug or exact display name."},
                "slug": {"type": "string", "description": "Alias for agent (slug from search results)."},
                "task": {"type": "string", "description": "The user's task to pair with the specialist context."},
            },
        },
        "handler": _t_load,
    },
    {
        "name": "delegate_agent",
        "description": "Get a delegation packet (specialist prompt + task) to run in an ISOLATED subagent. Use when you want context isolation / parallelism instead of doing the work inline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent slug or exact display name."},
                "slug": {"type": "string", "description": "Alias for agent (slug from search results)."},
                "task": {"type": "string", "description": "Concrete task for the specialist."},
            },
            "required": ["task"],
        },
        "handler": _t_delegate,
    },
]
_HANDLERS = {t["name"]: t["handler"] for t in TOOLS}
_TOOL_DEFS = [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]


# --- JSON-RPC / MCP stdio loop -----------------------------------------------

def _handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    mid = msg.get("id")
    # Notifications (no id) get no response.
    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion") or DEFAULT_PROTOCOL
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": proto,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _TOOL_DEFS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        handler = _HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            result = handler(params.get("arguments") or {})
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}],
                "isError": not result.get("success", True),
            }}
        except Exception as exc:  # defensive: never crash the server on one bad call
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"tool error: {exc}"}], "isError": True,
            }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is not None:  # unknown request
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None  # unknown notification


def _serve() -> int:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = _handle(msg)
        if response is not None:
            out.write(json.dumps(response, ensure_ascii=False) + "\n")
            out.flush()
    return 0


def _selftest() -> int:
    agents = _agents()
    assert len(agents) >= 250, f"expected >=250 agents, got {len(agents)}"
    slugs = [a["slug"] for a in agents]
    assert len(slugs) == len(set(slugs)), "duplicate slugs in roster"
    # search finds a security specialist for a security query
    res = _t_search({"query": "smart contract security audit", "limit": 5})
    assert res["success"] and res["count"] > 0, "search returned nothing"
    top = res["results"][0]["slug"]
    # inspect + load round-trip on the top hit
    ins = _t_inspect({"slug": top, "include_body": True})
    assert ins["success"] and ins["body"], "inspect failed"
    loaded = _t_load({"slug": top, "task": "audit this contract"})
    assert loaded["success"] and top in loaded["prompt"], "load failed"
    deleg = _t_delegate({"slug": top, "task": "audit"})
    assert deleg["success"] and "prompt" in deleg, "delegate failed"
    # division filter + empty query guard
    assert _t_search({"query": "x", "division": "nope"})["count"] == 0
    assert _t_search({"query": ""})["success"] is False
    assert _t_inspect({"slug": "does-not-exist"})["success"] is False
    print(f"selftest OK: {len(agents)} agents, top hit for security query = {top}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(_serve())
