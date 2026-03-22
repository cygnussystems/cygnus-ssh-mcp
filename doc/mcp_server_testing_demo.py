import asyncio
import sys
import os

# Ensure the main project directory is in the Python path
# This allows importing 'mcp_main' from the 'tests' directory
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

try:
    from fastmcp import Client
    # Import TextContent from mcp.types
    from mcp.types import TextContent
    from mcp.types import Tool
    # ToolInfo import removed as it's not used/available

    # Import the mcp instance directly from the main script
    from mcp_main import mcp
except ImportError as e:
    print(f"FATAL: Failed to import FastMCP Client, TextContent or mcp_main. Error: {e}", file=sys.stderr)
    print("Make sure fastmcp is installed and you are running from the correct directory.", file=sys.stderr)
    sys.exit(1)



async def run_basic_tests():
    """Runs basic in-process tests using fastmcp.Client."""
    print("Starting basic in-process tests...")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        print("Client created. Testing tools...")

        # --- Test listing tools ---
        try:
            print("\nListing available tools...")
            tools = await client.list_tools()
            print(f"  -> Found {len(tools)} tool(s):")

            assert isinstance(tools, list), "Expected list from list_tools()"
            assert tools, "No tools returned by list_tools()"

            expected_tool_ids = {"add", "subtract", "get_joke"}
            found_tool_ids = set()

            for tool in tools:
                # From your debug output:
                tool_id = tool.name
                description = tool.description

                assert isinstance(tool_id, str) and tool_id.strip(), "Invalid tool name"
                assert isinstance(description, str), "Description must be a string"

                print(f"    - ID: {tool_id}, Description: {description.strip()}")
                found_tool_ids.add(tool_id)

            missing = expected_tool_ids - found_tool_ids
            extra = found_tool_ids - expected_tool_ids

            assert not missing, f"Missing expected tools: {missing}"
            assert not extra, f"Unexpected tools found: {extra}"

            print("  -> List tools test passed successfully!")

        except Exception as e:
            print(f"  -> Error testing list_tools: {e}", file=sys.stderr)
            raise

        # --- Test 'add' tool ---
        try:
            print("\nTesting 'add' tool with 5.0 + 3.0...")
            add_params = {"number1": 5.0, "number2": 3.0}
            add_result = await client.call_tool("add", add_params)
            print(f"  -> Got result: {add_result} (type: {type(add_result)})")

            # Assertions updated to check the TextContent structure
            assert isinstance(add_result, list), f"Add test failed: Result is not a list, got {type(add_result)}"
            assert len(add_result) == 1, f"Add test failed: Result list length is not 1, got {len(add_result)}"

            content = add_result[0]
            assert isinstance(content, TextContent), f"Add test failed: Expected TextContent, got {type(content)}"
            # Compare the text attribute as a string
            assert content.text == '8.0', f"Add test failed: Expected text '8.0', got '{content.text}'"
            print("  -> Assertion passed!")
        except Exception as e:
            print(f"  -> Error testing 'add': {e}", file=sys.stderr)
            raise # Re-raise to fail the test run

        # --- Test 'subtract' tool ---
        try:
            print("\nTesting 'subtract' tool with 10.0 - 4.5...")
            subtract_params = {"number1": 10.0, "number2": 4.5}
            subtract_result = await client.call_tool("subtract", subtract_params)
            print(f"  -> Got result: {subtract_result} (type: {type(subtract_result)})")

            # Assertions updated to check the TextContent structure
            assert isinstance(subtract_result, list), f"Subtract test failed: Result is not a list, got {type(subtract_result)}"
            assert len(subtract_result) == 1, f"Subtract test failed: Result list length is not 1, got {len(subtract_result)}"

            content = subtract_result[0]
            assert isinstance(content, TextContent), f"Subtract test failed: Expected TextContent, got {type(content)}"
            # Compare the text attribute as a string
            assert content.text == '5.5', f"Subtract test failed: Expected text '5.5', got '{content.text}'"
            print("  -> Assertion passed!")
        except Exception as e:
            print(f"  -> Error testing 'subtract': {e}", file=sys.stderr)
            raise # Re-raise

        # --- Test 'get_joke' tool ---
        try:
            print("\nTesting 'get_joke' tool...")
            get_joke_params = {} # No parameters needed
            joke_result = await client.call_tool("get_joke", get_joke_params)
            print(f"  -> Got result: '{joke_result}' (type: {type(joke_result)})")

            # Assertions updated to check the TextContent structure
            assert isinstance(joke_result, list), f"Get Joke test failed: Result is not a list, got {type(joke_result)}"
            assert len(joke_result) == 1, f"Get Joke test failed: Result list length is not 1, got {len(joke_result)}"

            content = joke_result[0]
            assert isinstance(content, TextContent), f"Get Joke test failed: Expected TextContent, got {type(content)}"
            assert isinstance(content.text, str), f"Get Joke test failed: Expected text attribute to be a string, got {type(content.text)}"
            assert len(content.text) > 0, "Get Joke test failed: Result text string is empty"
            print("  -> Assertions passed!")
        except Exception as e:
            print(f"  -> Error testing 'get_joke': {e}", file=sys.stderr)
            raise # Re-raise

    print("\nAll basic tests completed successfully!")

if __name__ == "__main__":
    try:
        asyncio.run(run_basic_tests())
    except Exception as e:
        print(f"\nTest run failed with error: {e}", file=sys.stderr)
        sys.exit(1)
