# mcp-on-demand-tools

A Model Context Protocol (MCP) server that enables dynamic tool registration and execution powered by AI agents. Register custom tools on-the-fly and have them simulated by Goose recipe-based agents.

## Overview

This MCP server allows you to dynamically register new tools at runtime without server restarts. When you invoke a registered tool, the server uses a Goose recipe (`render_template.yaml`) to simulate the tool's execution and generate realistic output based on the tool's contract. This is useful for prototyping tool capabilities, testing workflows, or creating mock implementations before building real integrations.

## Components

### Resources

The server provides resources for monitoring registered tools:

- **Tool definition resources** (`tool://internal/{tool-name}`)
  - Returns the complete tool schema including name, description, parameters, expected output, and side effects
  - Includes call count statistics for monitoring usage

- **Stats resource** (`stats://internal/summary`)
  - Shows aggregate statistics: total tools registered, total calls made
  - Lists top 10 tools by call count

### Prompts

- **plan-with-tools**: A planning prompt that helps orchestrate tool usage
  - Arguments: `goal` (required), `notes` (optional)
  - Lists all currently registered tools and suggests registering new ones if needed

### Tools

#### Core Tool

- **register-tool**: Dynamically register a new on-demand tool
  - Required parameters:
    - `name`: Tool identifier (string)
    - `description`: What the tool does (string)
    - `paramSchema`: JSON object defining tool parameters. Each parameter should have `description` and `type` properties. Can be provided as an object or JSON string.
    - `expectedOutput`: Description of what the tool returns (string)
    - `sideEffects`: Description of any side effects, e.g., "Makes API call to weather service" or "None - simulated data generation" (string)

#### Dynamic Tools

Once registered, tools become immediately available for invocation. Each registered tool:
- Accepts a `params` argument (object or JSON string)
- Executes via a Goose recipe (`render_template.yaml`) which uses an AI model to generate realistic output
- Returns simulated output matching the expected output contract
- The server automatically notifies connected MCP clients when new tools are registered or when tool usage statistics change

## How It Works

1. **Register a tool** using the `register-tool` MCP tool with your desired schema
2. **Tool becomes available** immediately - the server sends notifications to connected clients
3. **Invoke the tool** with specific parameter values
4. **Goose recipe executes** to simulate the tool and generate realistic output based on the contract
5. **View statistics** via resources to monitor tool usage and call history

### Architecture

```
┌─────────────────┐
│  MCP Client     │
│  (Goose, etc)   │
└────────┬────────┘
         │
         │ register-tool
         ▼
┌─────────────────────────┐
│  MCP Server             │
│  (mcp-on-demand-tools)  │
│                         │
│  • Stores tool metadata │
│  • Tracks call history  │
│  • Provides resources   │
└────────┬────────────────┘
         │
         │ invoke: tool-name(params)
         ▼
┌─────────────────────────┐
│  Goose Recipe Runner    │
│  (render_template.yaml) │
│                         │
│  Simulates tool based   │
│  on contract & params   │
└─────────────────────────┘
```

### Example Workflow

```json
// Step 1: Register a weather forecast tool
{
  "name": "get-weather-forecast",
  "description": "Fetches weather forecast for a given location",
  "paramSchema": {
    "location": {
      "description": "City name or coordinates",
      "type": "string"
    },
    "days": {
      "description": "Number of days to forecast (1-7)",
      "type": "integer"
    }
  },
  "expectedOutput": "Weather forecast data including temperature, conditions, and precipitation",
  "sideEffects": "None - simulated data generation"
}

// Step 2: Tool is now available in MCP
// Step 3: Invoke it
{
  "params": {
    "location": "San Francisco",
    "days": 3
  }
}
// Step 4: Goose generates realistic weather data matching the contract
```

## Installation

### Prerequisites

**Required:**
- Python 3.12 or higher
- **[Goose](https://github.com/block/goose)** - Must be installed and available in your PATH
- `uv` package manager (recommended) or `pip`

**Important**: This server requires Goose to function. It uses Goose recipes to simulate tool execution, so you must have Goose installed before using this MCP server.

**Compatibility Note**: This server works best with MCP clients that support dynamic tool list updates (like Goose Desktop). Claude Code client does not automatically refresh the tool list when new tools are registered, so it may not work well with that client.

### Installing the MCP Server

Add the server configuration to your config file:

#### Option 1: Use Published Package (Recommended)

```json
{
  "mcpServers": {
    "mcp-on-demand-tools": {
      "command": "uvx",
      "args": ["mcp-on-demand-tools"]
    }
  }
}
```

#### Option 2: Development Setup

Clone the repository and use local installation:

```json
{
  "mcpServers": {
    "mcp-on-demand-tools": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/mcp-on-demand-tools",
        "run",
        "mcp-on-demand-tools"
      ]
    }
  }
}
```

### Installing for Other MCP Clients

For other MCP-compatible clients, configure them to run:

```bash
uvx mcp-on-demand-tools
```

Or for development:

```bash
uv --directory /path/to/mcp-on-demand-tools run mcp-on-demand-tools
```

## Development

### Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/mcp-on-demand-tools.git
cd mcp-on-demand-tools
```

2. Install dependencies:
```bash
uv sync
```

3. Run locally:
```bash
uv run mcp-on-demand-tools
```

### Building and Publishing

To prepare the package for distribution:

1. Build package distributions:
```bash
uv build
```

This creates source and wheel distributions in the `dist/` directory.

2. Publish to PyPI:
```bash
uv publish
```

**Note**: Publishing requires PyPI credentials via environment variables or command flags:
- Token: `--token` or `UV_PUBLISH_TOKEN`
- Or username/password: `--username`/`UV_PUBLISH_USERNAME` and `--password`/`UV_PUBLISH_PASSWORD`

### Automated Publishing

The project includes a GitHub Actions workflow that automatically:
- Builds the package on every push
- Publishes to TestPyPI on pushes to `main` branch
- Publishes to PyPI when you create a git tag (e.g., `v0.1.0`)

To publish a new version:
```bash
git tag v0.1.1
git push origin v0.1.1
```

### Debugging

Since MCP servers run over stdio, debugging can be challenging. For the best debugging experience, use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/mcp-on-demand-tools run mcp-on-demand-tools
```

Upon launching, the Inspector will display a URL that you can access in your browser to begin debugging.

### Project Structure

```
mcp-on-demand-tools/
├── src/
│   └── mcp_on_demand_tools/
│       ├── __init__.py           # Package entry point
│       ├── server.py             # Main MCP server implementation
│       └── recipes/
│           └── render_template.yaml  # Goose recipe for tool simulation
├── pyproject.toml                # Project metadata and dependencies
├── uv.lock                       # Locked dependencies
└── README.md                     # This file
```

## How Tool Execution Works

When you invoke a registered tool:

1. The MCP server receives the tool call with parameters
2. It constructs a context string with:
   - Tool name and description
   - Expected output contract
   - Side effects declaration
   - Input parameters (as JSON)
   - **Aggregate context**: History of previous calls to this tool (if any)
3. The server executes `goose run --recipe render_template.yaml` with these parameters
4. Goose's AI agent reads the contract and generates realistic output that matches the expected format
5. The output is extracted and returned to the MCP client

### Stateful Tool Execution

**New in v0.1.2**: The server now maintains call history for each registered tool. When you invoke a tool multiple times, the AI agent receives context from previous calls, enabling:

- **Conversational tools**: Tools can maintain context across multiple invocations
- **Incremental workflows**: Each call can build upon previous results
- **State-aware responses**: The AI can generate outputs that reference or continue from previous interactions

The aggregate context includes:
- Total number of previous calls
- Input parameters from each previous call
- Exit codes and outputs (first 200 characters)
- Call sequence/order

This allows the tool simulator to provide more coherent and contextual responses in multi-turn interactions.

## Use Cases

This server is ideal for:
- **Rapid prototyping**: Test tool concepts without implementing full functionality
- **API design exploration**: Experiment with tool interfaces before committing to implementation
- **Workflow testing**: Validate complex workflows with realistic mock data
- **Demonstration and documentation**: Show how tools would work without building them
- **Placeholder tools**: Create temporary tool implementations during development

## Changelog

### v0.1.1 (2025-10-12)
- **Added aggregate context support**: Tool executions now receive history from previous calls
- Enables stateful, conversational tool behavior across multiple invocations
- AI agent can now generate contextually-aware responses based on call history

### v0.1.0
- Initial release
- Initial MCP server implementation
- Dynamic tool registration
- Goose recipe-based tool simulation


## License

See [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
