#!/usr/bin/env python3

import asyncio
import json
from mcp import ClientSession
from mcp.client.stdio import stdio_client

async def test_server():
    # Start the server as a subprocess
    async with stdio_client(['uv', 'run', 'mcp-on-demand-tools']) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the connection
            await session.initialize()
            
            # List available tools
            tools = await session.list_tools()
            print("Available tools:")
            for tool in tools:
                print(f"  - {tool.name}: {tool.description[:50]}...")
            
            # Test search-tool-repository
            print("\n--- Testing search-tool-repository ---")
            result = await session.call_tool("search-tool-repository", {"query": "weather"})
            print("Search results:")
            print(result[0].content if result else "No results")
            
            # Test install-tool
            print("\n--- Testing install-tool ---")
            install_result = await session.call_tool("install-tool", {
                "name": "get-weather-forecast",
                "description": "Provides 7-day weather forecasts for any location",
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
                "toolBehaviorType": "read_idempotent",
                "sideEffects": "None - simulated data generation"
            })
            print("Install result:")
            print(install_result[0].content if install_result else "No result")
            
            # List tools again to see the new tool
            print("\n--- Available tools after installation ---")
            tools = await session.list_tools()
            for tool in tools:
                print(f"  - {tool.name}: {tool.description[:50]}...")
            
            # Test list-installed-tools
            print("\n--- Testing list-installed-tools ---")
            list_result = await session.call_tool("list-installed-tools", {})
            print("Installed tools:")
            print(list_result[0].content if list_result else "No result")
            
            # Test the newly installed tool
            print("\n--- Testing the new tool ---")
            weather_result = await session.call_tool("get-weather-forecast", {
                "params": {
                    "location": "San Francisco",
                    "days": 3
                }
            })
            print("Weather forecast result:")
            print(weather_result[0].content if weather_result else "No result")
            
            # Test uninstall-tool
            print("\n--- Testing uninstall-tool ---")
            uninstall_result = await session.call_tool("uninstall-tool", {
                "name": "get-weather-forecast"
            })
            print("Uninstall result:")
            print(uninstall_result[0].content if uninstall_result else "No result")

if __name__ == "__main__":
    asyncio.run(test_server())
