import asyncio, json, time, os
from typing import Any, Dict, List, Tuple
from pathlib import Path

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
from pydantic import AnyUrl
import mcp.server.stdio

# ==============================================================================
# Goal
# - Agent registers a tool with a Goose recipe for calls
# - Reading a *synth resource* triggers Goose with a prompt template
#   that is filled from saved state (tool metadata and prior calls)
# ==============================================================================

# --- Configuration: Define a robust path to the recipe file ---
SCRIPT_DIR = Path(__file__).parent.resolve()
RENDER_RECIPE_PATH = str(SCRIPT_DIR / "recipes" / "render_template.yaml")

server = Server("on-demand-tools")

# tools[name] = {
#   "description": str,
#   "paramSchema": dict,        # JSON Schema for {"params": {...}} on call
#   "expectedOutput": str,      # contract fallback if Goose stdout empty
#   "sideEffects": str,
#   "promptTemplate": str,      # Template string injected into renderRecipe (param 'template')
#   "calls": [ { "params": dict, "exit_code": int, "stdout": str, "stderr": str, "ts": float } ]
# }
tools: Dict[str, Dict[str, Any]] = {}

# ------------------------------------------------------------------------------
# Goose helper
# ------------------------------------------------------------------------------
def _json_block(d: Dict[str, Any]) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False)

def _extract_goose_output(goose_stdout: str) -> str:
    """Extracts the final result from Goose's stdout, filtering out debug lines."""
    if not goose_stdout.strip():
        return ""
    lines = goose_stdout.strip().split('\n')
    try:
        # Find the line containing "working directory:" and take everything after it
        idx = next(i for i, line in enumerate(lines) if "working directory:" in line)
        final_result = "\n".join(lines[idx+1:]).strip()
        return final_result
    except StopIteration:
        # If the marker isn't found for some reason, fall back to just the last line
        return lines[-1]
    
async def _run_goose(recipe: str, params: dict[str, any],
                     no_session: bool = True,
                     timeout_sec: int | None = None) -> Tuple[int, str, str, List[str]]:
    """
    Execute Goose with robust error handling.
    """
    # Build command
    cmd = ["goose", "run"]
    if no_session:
        cmd.append("--no-session")
    
    # The recipe path is now expected to be absolute
    cmd += ["--recipe", recipe]

    for k, v in (params or {}).items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        cmd += ["--params", f"{k}={v}"]

    # Spawn subprocess with timeout
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec or 90)
        rc = proc.returncode
    except asyncio.TimeoutError:
        rc, out_b, err_b = 124, b"", b"timeout"
        proc.kill() # Ensure the process is terminated
    except FileNotFoundError:
        rc, out_b, err_b = 127, b"", b"goose binary not found"

    return rc or 0, out_b.decode(errors="replace"), err_b.decode(errors="replace"), cmd

def _json_block(d: Dict[str, Any]) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False)

# ------------------------------------------------------------------------------
# Resources
#   tool://internal/<name>          -> definition
#   stats://internal/summary        -> summary
# ------------------------------------------------------------------------------

@server.list_resources()
async def handle_list_resources() -> List[types.Resource]:
    out: List[types.Resource] = []
    for n, meta in tools.items():
        out.append(types.Resource(
            uri=AnyUrl(f"tool://internal/{n}"),
            name=f"Tool: {n}",
            description=meta.get("description") or "(no description)",
            mimeType="application/json",
        ))
    out.append(types.Resource(
        uri=AnyUrl("stats://internal/summary"),
        name="Stats",
        description="Counts and top calls",
        mimeType="application/json",
    ))
    return out

@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> str:
    scheme = uri.scheme
    path = (uri.path or "").lstrip("/")

    if scheme == "tool":
        name = path
        if name not in tools:
            raise ValueError(f"Unknown tool: {name}")
        meta = tools[name].copy()
        meta["callsRecorded"] = len(meta.get("calls", []))
        meta.pop("calls", None)
        return _json_block({"name": name, **meta})

    if scheme == "stats":
        total_tools = len(tools)
        total_calls = sum(len(m.get("calls", [])) for m in tools.values())
        top = sorted(
            ((n, len(m.get("calls", []))) for n, m in tools.items()),
            key=lambda t: t[1],
            reverse=True
        )[:10]
        return _json_block({"total_tools": total_tools, "total_calls": total_calls, "top": top})

    raise ValueError(f"Unsupported URI scheme: {scheme}")


# ------------------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------------------

@server.list_prompts()
async def handle_list_prompts() -> List[types.Prompt]:
    return [
        types.Prompt(
            name="plan-with-tools",
            description="Plan a solution. If a needed tool is missing, call 'register-tool'.",
            arguments=[
                types.PromptArgument(name="goal", description="Objective", required=True),
                types.PromptArgument(name="notes", description="Constraints", required=False),
            ],
        )
    ]

@server.get_prompt()
async def handle_get_prompt(name: str, arguments: Dict[str, str] | None) -> types.GetPromptResult:
    if name != "plan-with-tools":
        raise ValueError(f"Unknown prompt: {name}")
    args = arguments or {}
    known = "\n".join(
        f"- {n}: {m.get('description') or '(no description)'}"
        for n, m in tools.items()
    ) or "(no tools yet)"
    text = (
        "If a needed capability is missing, call MCP tool 'register-tool'.\n\n"
        f"Goal:\n{args.get('goal', '')}\n\nNotes:\n{args.get('notes') or '(none)'}\n\n"
        f"Known tools:\n{known}"
    )
    return types.GetPromptResult(
        description="Plan and use tools",
        messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text=text))],
    )

# ------------------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------------------

@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    base = [
        types.Tool(
            name="register-tool",
            description="Register a tool request for a background agent to synthesize. paramSchema is a JSON object containing dictionaries with keys `description` and `type`",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "paramSchema": {"type": "object"},
                    "expectedOutput": {"type": "string"},
                    "sideEffects": {"type": "string"},
                    "promptTemplate": {"type": "string"},
                },
                "required": ["name", "description", "paramSchema", "expectedOutput", "sideEffects"],
                "additionalProperties": False,
            },
        )
    ]
    dynamic = [
        types.Tool(
            name=n,
            description=f"{m.get('description')} | side effects: {m['sideEffects']}",
            inputSchema={
                "type": "object",
                "properties": {
                    "params": {
                        "anyOf": [
                            {"type": "object"},
                            {"type": "string"}
                        ],
                        "description": "Key-value parameters for the tool. Accepts an object or a JSON string."
                    },
                },
                "required": ["params"],
                "additionalProperties": False,
            },
        )
        for n, m in tools.items()
    ]

    return base + dynamic

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: Dict | None
) -> List[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if name == "register-tool":
        args = arguments or {}
        if not all(k in args for k in ["name", "description", "paramSchema", "expectedOutput", "sideEffects"]):
            raise ValueError("Missing one or more required arguments for register-tool")
        
        n = args["name"]
        
        param_schema = args["paramSchema"]
        if isinstance(param_schema, str):
            try:
                param_schema = json.loads(param_schema)
            except json.JSONDecodeError:
                raise ValueError("The 'paramSchema' argument was a string but not valid JSON.")

        tools[n] = {
            "description": args["description"],
            "paramSchema": param_schema, # Use the potentially parsed object
            "expectedOutput": args["expectedOutput"],
            "sideEffects": args["sideEffects"],
            "calls": [],
        }
        await server.request_context.session.send_resource_list_changed()
        await server.request_context.session.send_tool_list_changed()
        return [types.TextContent(type="text", text=f"Registered tool '{n}'.")]

    # Dynamic path: execute Goose for the tool call
    if name not in tools:
        raise ValueError(f"Unknown tool: {name}")

    meta = tools[name]
    args = arguments or {}
    raw_params = args.get("params", {})

    # Normalize params: accept object or JSON string
    if isinstance(raw_params, str):
        try:
            params = json.loads(raw_params)
        except json.JSONDecodeError:
            raise ValueError("`params` was a string but not valid JSON.")
    elif isinstance(raw_params, dict):
        params = raw_params
    else:
        raise ValueError("`params` must be an object or a JSON string.")
    single_call_context_str = (
            f"Mode: single\n"
            f"Inputs (JSON): {json.dumps(params)}\n"
            f"Return only the output payload that satisfies the contract."
        ).replace("\n", " ")

    full_goose_params = {
        "tool_name": name,
        "tool_description": meta.get("description", "(no description)"), # Use .get() for safety
        "expected_output": meta["expectedOutput"],
        "side_effects": meta["sideEffects"],
        "single_call_context": single_call_context_str,
    }
    
    # Run Goose
    rc, out, err, cmds = await _run_goose(
        RENDER_RECIPE_PATH,
        full_goose_params,
    )

    # Record and respond (no changes needed here)
    meta["calls"].append({
        "params": params,
        "exit_code": rc,
        "stdout": out,
        "stderr": err,
        "attempted_cmds": cmds,
        "ts": time.time(),
    })
    await server.request_context.session.send_resource_list_changed()

    if rc == 0 and out.strip():
        final_result = _extract_goose_output(out)
        return [types.TextContent(type="text", text=final_result)]
    msg = (
        f"[{name}] exit_code={rc}\n"
        f"stderr:\n{(err[:2000] if err else '(empty)')}"
    )
    return [types.TextContent(type="text", text=msg)]


# ------------------------------------------------------------------------------
# Entry
# ------------------------------------------------------------------------------

async def main():
    """Initializes and runs the MCP server."""
    async with mcp.server.stdio.stdio_server() as (rs, ws):
        await server.run(
            rs, ws,
            InitializationOptions(
                server_name="on-demand-tools",
                server_version="0.1.1",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(
                        tools_changed=True,
                        resources_changed=True,
                    ),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())