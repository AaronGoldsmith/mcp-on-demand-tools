import asyncio, json, time
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
RECIPE_DIR = Path(SCRIPT_DIR / "recipes" ).resolve()
RENDER_RECIPE_PATH = str(RECIPE_DIR / "render_template.yaml")
SEARCH_TOOLS_RECIPE_PATH = str(RECIPE_DIR / "search_tools.yaml")


server = Server("on-demand-tools")

# tools[name] = {
#   "description": str,
#   "paramSchema": dict,        # JSON Schema for {"params": {...}} on call
#   "expectedOutput": str,      # contract fallback if Goose stdout empty
#   "sideEffects": str,
#   "toolTypeBehavior": str,    # defines stateful/stateless behavior
#   "calls": [ { "params": dict, "exit_code": int, "stdout": str, "stderr": str, "ts": float } ]
# }
tools: Dict[str, Dict[str, Any]] = {}

# Directory for persistent tool storage
INSTALLED_TOOLS_DIR = Path.home() / ".mcp-on-demand-tools" / "installed"
INSTALLED_TOOLS_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------------------
# Goose helper
# ------------------------------------------------------------------------------
def _yaml_safe_string(s: str) -> str:
    """
    Escape a string to be safely used as a YAML parameter value.
    This replaces problematic characters that could break YAML parsing.
    """
    if not s:
        return s
    # Replace problematic characters
    s = s.replace('\\', '\\\\')  # Escape backslashes first
    s = s.replace('"', '\\"')     # Escape double quotes
    s = s.replace("```", "\\`\\`\\`") # Escape triple backticks
    s = s.replace("'", "''")      # Escape single quotes (YAML style)
    s = s.replace('\n', ' ')      # Replace newlines with spaces
    s = s.replace('\r', ' ')      # Replace carriage returns
    s = s.replace('\t', ' ')      # Replace tabs
    # Remove other control characters
    s = ''.join(c if ord(c) >= 32 or c in '\n\r\t' else ' ' for c in s)
    return s


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
    
async def _run_goose(recipe: str, params: dict[str, Any],
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


# ------------------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------------------

@server.list_prompts()
async def handle_list_prompts() -> List[types.Prompt]:
    return [
        types.Prompt(
            name="plan-with-tools",
            description="Plan a solution. If a needed tool is missing, search for it and install it.",
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
        "If a needed capability is missing, first call search-tool-repository to find an existing package. "
        "If you find one (or if you have a clear idea of the tool you need), call install-tool to add it to my session.\n\n"
        f"Goal:\n{args.get('goal', '')}\n\nNotes:\n{args.get('notes') or '(none)'}\n\n"
        f"Known Installed Tools:\n{known}"
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
            name="install-tool",
            description="""Downloads and installs a tool from the central tool repository with over 26,000 community built plugins. 
            If a plugin has no matches, the query is automatically turned into a request for our AI engineers to build.
            
            Parameters match those of the previous 'register-tool' command.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "paramSchema": {"type": "object"},
                    "expectedOutput": {"type": "string"},
                    "toolBehaviorType": {"type": "string"},
                    "sideEffects": {"type": "string"},
                },
                "required": ["name", "description", "paramSchema", "expectedOutput", "toolBehaviorType"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="search-tool-repository",
            description="Searches the central tool repository for available packages. Returns a list of matching tool names and their descriptions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query term (e.g., 'weather', 'file system')"}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="list-installed-tools",
            description="Lists all remote tools currently installed and active in this session.",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="uninstall-tool",
            description="Removes a remote tool from the current session, freeing up resources.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the tool to uninstall"}
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        )
    ]
    dynamic = [
        types.Tool(
            name=n,
            description=f"{m.get('description')} | side effects: {m.get('sideEffects')}",
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
    # Handle new package manager style tools
    if name == "install-tool":
        args = arguments or {}
        required_fields = ["name", "description", "paramSchema", "expectedOutput", "toolBehaviorType"]
        if not all(k in args for k in required_fields):
            raise ValueError(f"Missing one or more required arguments for {name}")
        
        tool_name = args["name"]
        
        param_schema = args["paramSchema"]
        if isinstance(param_schema, str):
            try:
                param_schema = json.loads(param_schema)
            except json.JSONDecodeError:
                raise ValueError("The 'paramSchema' argument was a string but not valid JSON.")

        # Save tool to persistent storage
        tool_data = {
            "name": tool_name,
            "description": args["description"],
            "paramSchema": param_schema,
            "expectedOutput": args["expectedOutput"],
            "toolTypeBehavior": args["toolBehaviorType"],
            "sideEffects": args.get("sideEffects"),
        }
        
        tool_file_path = INSTALLED_TOOLS_DIR / f"{tool_name}.json"
        try:
            with open(tool_file_path, 'w') as f:
                json.dump(tool_data, f, indent=2)
        except IOError as e:
            # If we can't save to persistent storage, we'll still register it in memory
            print(f"Warning: Could not save tool to persistent storage: {e}")

        # Register tool in memory (with empty calls list)
        tools[tool_name] = {
            "description": args["description"],
            "paramSchema": param_schema,
            "expectedOutput": args["expectedOutput"],
            "toolTypeBehavior": args["toolBehaviorType"],
            "sideEffects": args.get("sideEffects"),
            "calls": [],
        }
        await server.request_context.session.send_tool_list_changed()
        
        # Return realistic installation response
        return [types.TextContent(type="text", text=(
            f"Connecting to tool repository...\n"
            f"Searching for matching packages...\n"
            f"No exact pre-built package found for '{tool_name}'.\n"
            f"Attempting to configure a new simulated tool based on specifications...\n"
            f"Downloading dependencies... [1/2]\n"
            f"Configuring plugin... [2/2]\n"
            f"Success! Tool {tool_name} has been installed and is ready for use."
        ))]

    elif name == "search-tool-repository":
        args = arguments or {}
        query = args.get("query", "")
        
        # Call run goose recipe with SEARCH_TOOLS_RECIPE_PATH
        rc, out, err, cmds = await _run_goose(
            SEARCH_TOOLS_RECIPE_PATH,
            {"query": query},
        )

        
        if rc == 0 and out.strip():
            out_lines = out.split('\n')
            cleaned_lines = [
                stripped
                for stripped in (line.strip() for line in out_lines)
                if not (
                    stripped.startswith("running without session")
                    or "working directory:" in stripped
                )
            ]
            out = "\n".join(cleaned_lines)
            return [types.TextContent(type="text", text=out)]
        else:
            return [types.TextContent(type="text", text=(
                f"Error: Goose execution failed with exit code {rc}.\n"
                f"Goose stderr:\n{err}"
            ))]
        
    elif name == "list-installed-tools":
        if not tools:
            return [types.TextContent(type="text", text="No tools are currently installed.")]
        
        result_text = "Installed tools:\n"
        for tool_name, tool_meta in tools.items():
            result_text += f"- {tool_name}: {tool_meta.get('description', '(no description)')}\n"
            
        return [types.TextContent(type="text", text=result_text.strip())]
    
    elif name == "uninstall-tool":
        args = arguments or {}
        tool_name = args.get("name")
        
        if not tool_name:
            raise ValueError("Missing required argument 'name' for uninstall-tool")
            
        if tool_name not in tools:
            return [types.TextContent(type="text", text=f"Tool '{tool_name}' is not installed.")]
        
        # Remove from memory
        del tools[tool_name]
        
        # Remove from persistent storage
        tool_file_path = INSTALLED_TOOLS_DIR / f"{tool_name}.json"
        if tool_file_path.exists():
            try:
                tool_file_path.unlink()
            except IOError as e:
                print(f"Warning: Could not remove tool from persistent storage: {e}")
        
        await server.request_context.session.send_tool_list_changed()
        return [types.TextContent(type="text", text=f"Tool '{tool_name}' has been successfully uninstalled.")]

    # Handle legacy register-tool for backward compatibility
    elif name == "register-tool":
        args = arguments or {}
        if not all(k in args for k in ["name", "description", "paramSchema", "expectedOutput", "toolBehaviorType"]):
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
            "toolTypeBehavior": args["toolBehaviorType"],
            "sideEffects": args.get("sideEffects"),
            "calls": [],
        }
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

    # Build aggregate context from call history
    aggregate_context_str = "N/A"
    if meta["calls"]:
        call_summaries = []
        for idx, call in enumerate(meta["calls"], 1):
            call_summary = (
                f"Call {idx}: "
                f"params={json.dumps(call['params'])} | "
                f"output={call['stdout'][:200] if call['stdout'] else '(empty)'}..."
            )
            call_summaries.append(call_summary)
        # Replace newlines with spaces to avoid breaking YAML parsing
        aggregate_context_str = (
            f"Mode: aggregate | "
            f"Previous calls ({len(meta['calls'])}): " + " || ".join(call_summaries)
        )

    # Prepare full Goose parameters
    full_goose_params = {
        "tool_name": name,
        "tool_description": _yaml_safe_string(meta["description"]),
        "expected_output": _yaml_safe_string(meta["expectedOutput"]),
        "tool_type_behavior": _yaml_safe_string(meta["toolTypeBehavior"]),
        "side_effects": _yaml_safe_string(meta.get("sideEffects")),
        "single_call_context": _yaml_safe_string(single_call_context_str), # Apply YAML-safe escaping
        "aggregate_context": _yaml_safe_string(aggregate_context_str),
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

def load_installed_tools():
    """Load previously installed tools from persistent storage."""
    if not INSTALLED_TOOLS_DIR.exists():
        return
    
    for tool_file in INSTALLED_TOOLS_DIR.glob("*.json"):
        try:
            with open(tool_file, 'r') as f:
                tool_data = json.load(f)
                tool_name = tool_data.get('name')
                if tool_name:
                    # Remove the 'name' key as it's used as the dictionary key
                    tool_data.pop('name', None)
                    # Initialize empty calls list for persistent tools
                    tool_data['calls'] = []
                    tools[tool_name] = tool_data
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load tool from {tool_file}: {e}")

async def main():
    """Initializes and runs the MCP server."""
    # Load previously installed tools
    load_installed_tools()
    
    async with mcp.server.stdio.stdio_server() as (rs, ws):
        await server.run(
            rs, ws,
            InitializationOptions(
                server_name="on-demand-tools",
                server_version="0.1.3",
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