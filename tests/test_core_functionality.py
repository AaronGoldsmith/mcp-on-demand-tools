import pytest
import json
from typing import Dict, Any
from unittest.mock import patch

# Import the server module
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Import specific functions we want to test
from mcp_on_demand_tools.server import tools, _yaml_safe_string, _extract_goose_output


class TestCoreFunctionality:
    """Test core functionality without complex server context."""

    def setup_method(self):
        """Clear the tools dictionary before each test."""
        tools.clear()

    def test_tool_registration_data_structure(self):
        """Test that we can register a tool and it has the correct structure."""
        # Define tool parameters
        tool_name = "test-tool"
        tool_metadata = {
            "description": "A test tool for validation",
            "paramSchema": {
                "param1": {
                    "description": "First parameter",
                    "type": "string"
                }
            },
            "expectedOutput": "Test output string",
            "sideEffects": "None - simulated data generation",
            "calls": []
        }

        # Register the tool by directly adding to the tools dictionary
        tools[tool_name] = tool_metadata
        
        # Verify the tool was registered
        assert "test-tool" in tools
        registered_tool = tools["test-tool"]
        assert registered_tool["description"] == "A test tool for validation"
        assert registered_tool["expectedOutput"] == "Test output string"
        assert registered_tool["sideEffects"] == "None - simulated data generation"
        assert registered_tool["calls"] == []

    def test_tool_call_recording(self):
        """Test that we can record tool calls."""
        # Register a tool
        tool_name = "test-call-tool"
        tools[tool_name] = {
            "description": "A tool for testing calls",
            "paramSchema": {},
            "expectedOutput": "Test call output",
            "sideEffects": "None",
            "calls": []
        }
        
        # Simulate a tool call being recorded
        call_record = {
            "params": {"param1": "value1", "param2": "value2"},
            "exit_code": 0,
            "stdout": "test output",
            "stderr": "",
            "attempted_cmds": ["cmd1", "cmd2"],
            "ts": 1234567890
        }
        
        tools[tool_name]["calls"].append(call_record)
        
        # Verify the call was recorded
        assert len(tools[tool_name]["calls"]) == 1
        recorded_call = tools[tool_name]["calls"][0]
        assert recorded_call["params"] == {"param1": "value1", "param2": "value2"}
        assert recorded_call["exit_code"] == 0
        assert recorded_call["stdout"] == "test output"

    def test_yaml_safe_string(self):
        """Test the YAML safe string function."""
        # Test normal string
        result = _yaml_safe_string("normal string")
        assert result == "normal string"
        
        # Test string with quotes
        result = _yaml_safe_string('string with "quotes"')
        assert result == 'string with \\"quotes\\"'
        
        # Test string with newlines
        result = _yaml_safe_string("string\nwith\nnewlines")
        assert result == "string with newlines"
        
        # Test string with tabs
        result = _yaml_safe_string("string\twith\ttabs")
        assert result == "string with tabs"
        
        # Test empty string
        result = _yaml_safe_string("")
        assert result == ""

    def test_extract_goose_output(self):
        """Test extracting output from Goose stdout."""
        # Test normal output with working directory marker
        goose_output = """Some debug info
working directory: /tmp/test
This is the actual result
More result data"""
        
        result = _extract_goose_output(goose_output)
        assert result == "This is the actual result\nMore result data"
        
        # Test output without marker (fallback to last line)
        goose_output = """Some debug info
No marker here
Final result line"""
        
        result = _extract_goose_output(goose_output)
        assert result == "Final result line"
        
        # Test empty output
        result = _extract_goose_output("")
        assert result == ""
