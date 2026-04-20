"""
Tests for file operations with special Unicode characters commonly found in LLM outputs.

This module tests the SSH MCP server's ability to handle:
- Emojis (✅, ❌, 🎉, 🚀, etc.)
- Bullet points and list markers (•, ◦, ▪, ▸, ►, etc.)
- Checkboxes and task markers (☐, ☑, ☒, ✓, ✗)
- Mathematical symbols (→, ←, ≥, ≤, ≠, ∞, etc.)
- Currency and special symbols (€, £, ¥, ©, ®, ™)
- Box drawing characters (─, │, ┌, ┐, └, ┘, etc.)
- Quotes and punctuation (", ", ', ', —, –, …)
- Accented characters and international text
"""

import pytest
import json
import logging
import time
from conftest import (
    print_test_header,
    print_test_footer,
    make_connection,
    disconnect_ssh,
    remote_temp_path,
    extract_result_text,
    cleanup_remote_path,
    cleanup_file_command,
    read_file_command,
    skip_on_windows
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)


# Test content with various Unicode characters
UNICODE_TEST_CASES = {
    "emojis": {
        "description": "Common emojis used in LLM outputs",
        "content": """# Status Report 🎉

## Completed Tasks ✅
- Task 1 is done ✅
- Task 2 is done ✅
- Bug fix applied 🔧

## Pending Tasks ⏳
- Task 3 needs work ⚠️
- Task 4 blocked ❌

## Notes 📝
- Great progress! 🚀
- Team morale is high 😊
- Deadline approaching ⏰
"""
    },
    "bullets_and_markers": {
        "description": "Various bullet points and list markers",
        "content": """# Project Overview

• Main bullet point
  ◦ Sub-bullet level 1
    ▪ Sub-bullet level 2
      ▸ Sub-bullet level 3

► Section One
  ▻ Detail A
  ▻ Detail B

★ Important item
☆ Less important item

▶ Action items:
  ▷ First action
  ▷ Second action
"""
    },
    "checkboxes": {
        "description": "Checkbox and task markers",
        "content": """# Task List

☐ Unchecked task
☑ Checked task (filled)
☒ Crossed out task
✓ Checkmark
✗ X mark
✔ Heavy checkmark
✘ Heavy X mark

## Progress
[✓] Step 1 complete
[✓] Step 2 complete
[✗] Step 3 failed
[ ] Step 4 pending
"""
    },
    "math_and_arrows": {
        "description": "Mathematical symbols and arrows",
        "content": """# Mathematical Expressions

Arrows: → ← ↑ ↓ ↔ ⇒ ⇐ ⇔ ↗ ↘ ↙ ↖

Comparisons: ≥ ≤ ≠ ≈ ≡ ∝

Math: ∞ ∑ ∏ √ ∫ ∂ ∆ ∇ ∈ ∉ ⊂ ⊃ ∪ ∩

Greek: α β γ δ ε ζ η θ λ μ π σ φ ω

Superscript: x² y³ n⁴ 10⁵
Subscript: H₂O CO₂ x₁ x₂

Fractions: ½ ⅓ ¼ ⅕ ⅙ ⅛
"""
    },
    "currency_and_symbols": {
        "description": "Currency and special symbols",
        "content": """# Financial Report

Currency symbols: $ € £ ¥ ₹ ₽ ₿ ¢

Legal symbols: © ® ™ § ¶

Temperature: 25°C / 77°F

Other symbols: † ‡ • ‰ № ℃ ℉ Ω ℮

Music: ♩ ♪ ♫ ♬ ♭ ♮ ♯

Cards: ♠ ♣ ♥ ♦

Misc: ⚡ ⚠ ☢ ☣ ♻ ⚙ ☀ ☁ ☂ ★ ☆
"""
    },
    "box_drawing": {
        "description": "Box drawing characters for tables/diagrams",
        "content": """# System Architecture

┌─────────────────────────────────┐
│         Main Server             │
├─────────────────────────────────┤
│  ┌─────────┐    ┌─────────┐    │
│  │ Service │───▶│ Database│    │
│  │    A    │    │         │    │
│  └─────────┘    └─────────┘    │
│       │              ▲          │
│       ▼              │          │
│  ┌─────────┐    ┌─────────┐    │
│  │ Service │───▶│  Cache  │    │
│  │    B    │    │         │    │
│  └─────────┘    └─────────┘    │
└─────────────────────────────────┘

Flow: Input ─────▶ Process ─────▶ Output
            │                │
            └──── Feedback ──┘
"""
    },
    "smart_quotes_and_punctuation": {
        "description": "Smart quotes and special punctuation",
        "content": """# Quotations and Punctuation

"This is a quote with smart double quotes"
'This is a quote with smart single quotes'

— This is an em dash
– This is an en dash

Ellipsis… and more text here…

«Guillemets used in French»
„German style quotes"
『Japanese brackets』

Special spaces: word word (non-breaking space test)
"""
    },
    "international_text": {
        "description": "International characters and scripts",
        "content": """# International Greetings

English: Hello, World!
Spanish: ¡Hola, Mundo! ¿Cómo estás?
French: Bonjour le Monde! Ça va?
German: Hallo Welt! Größe
Portuguese: Olá Mundo! São Paulo
Swedish: Hallå Världen! Malmö
Polish: Witaj Świecie! Łódź
Czech: Ahoj světe! Příliš žluťoučký kůň

Russian: Привет мир!
Chinese: 你好世界！
Japanese: こんにちは世界！
Korean: 안녕하세요 세계!
Arabic: مرحبا بالعالم
Hebrew: שלום עולם
Thai: สวัสดีโลก
"""
    },
    "code_with_unicode": {
        "description": "Code snippets with Unicode in strings/comments",
        "content": '''# Python Code with Unicode

def greet(name: str) -> str:
    """
    Greet a user with emojis! 🎉

    Args:
        name: User's name (supports Unicode: José, 北京, Москва)

    Returns:
        Greeting message with ✨ sparkles ✨
    """
    # Status indicators: ✅ success, ❌ error, ⚠️ warning
    return f"Hello, {name}! 👋 Welcome! 🚀"

# Constants with symbols
PI = 3.14159  # π ≈ 3.14159
INFINITY = float('inf')  # ∞
EPSILON = 1e-10  # ε → 0

# Box drawing for output
BORDER = "═" * 40
CORNER_TL = "╔"
CORNER_TR = "╗"
'''
    },
    "mixed_heavy_unicode": {
        "description": "Heavy mix of all Unicode types",
        "content": """# 📋 Complete Project Status Report 📋

═══════════════════════════════════════════════════════
║ Project: "Système Überprüfung" — Version 2.0 ™      ║
║ Status: ✅ Active │ Priority: ★★★★☆              ║
═══════════════════════════════════════════════════════

## 🎯 Objectives

• Primary Goal → Achieve 99.9% uptime
  ◦ Current: 99.7% ⚠️
  ◦ Target: ≥99.9% ✓

• Secondary Goals:
  ▸ Reduce latency ↓50%
  ▸ Increase throughput ↑100%
  ▸ Cost reduction: $10,000 → $7,500 (−25%)

## ☑️ Task Checklist

[✓] Phase 1: Design — "Conceptualização"
[✓] Phase 2: Development — 开发阶段
[✗] Phase 3: Testing — Тестирование (blocked)
[ ] Phase 4: Deployment — نشر

## 📊 Metrics

┌──────────────┬─────────┬─────────┬────────┐
│ Metric       │ Target  │ Actual  │ Status │
├──────────────┼─────────┼─────────┼────────┤
│ Response (ms)│ ≤100    │ 95      │ ✅     │
│ Error Rate   │ <0.1%   │ 0.05%   │ ✅     │
│ CPU Usage    │ ≤80%    │ 85%     │ ⚠️     │
│ Memory (GB)  │ ≤16     │ 18      │ ❌     │
└──────────────┴─────────┴─────────┴────────┘

## 💰 Budget (€)

• Allocated: €50,000
• Spent: €42,500 (85%)
• Remaining: €7,500 ✓

© 2024 Acme Corp™ — All Rights Reserved®
Contact: support@例え.jp | Téléphone: +33 1 23 45 67 89
"""
    }
}


@pytest.mark.asyncio
@skip_on_windows
async def test_file_write_read_emojis(mcp_test_environment):
    """Test writing and reading files with emojis."""
    print_test_header("Testing file write/read with emojis")
    logger.info("Starting emoji file test")

    test_case = UNICODE_TEST_CASES["emojis"]
    test_file = remote_temp_path("test_emojis") + ".md"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Write the file
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_case["content"]
            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write file: {write_json}"
            logger.info("Successfully wrote emoji content to file")

            # Read the file back
            read_result = await client.call_tool("ssh_cmd_run", {
                "command": read_file_command(test_file),
                "io_timeout": 10.0
            })
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read file: {read_json}"

            # Verify content matches (normalize line endings for comparison)
            read_content = read_json['output'].rstrip('\n').replace('\r\n', '\n')
            expected_content = test_case["content"].rstrip('\n')
            assert read_content == expected_content, \
                f"Content mismatch!\nExpected:\n{expected_content}\n\nGot:\n{read_content}"

            logger.info("Emoji content verified successfully")

        finally:
            # Cleanup
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_file_command(test_file),
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_write_read_bullets(mcp_test_environment):
    """Test writing and reading files with bullet points and markers."""
    print_test_header("Testing file write/read with bullets and markers")
    logger.info("Starting bullet points file test")

    test_case = UNICODE_TEST_CASES["bullets_and_markers"]
    test_file = remote_temp_path("test_bullets") + ".md"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Write the file
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_case["content"],
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write file: {write_json}"

            # Read the file back
            read_result = await client.call_tool("ssh_cmd_run", {
                "command": read_file_command(test_file),
                "io_timeout": 10.0
            })
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read file: {read_json}"

            # Verify content matches
            assert read_json['output'].rstrip('\n').replace('\r\n', '\n') == test_case["content"].rstrip('\n'), "Content mismatch for bullet points"

            logger.info("Bullet points content verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_write_read_checkboxes(mcp_test_environment):
    """Test writing and reading files with checkbox characters."""
    print_test_header("Testing file write/read with checkboxes")
    logger.info("Starting checkboxes file test")

    test_case = UNICODE_TEST_CASES["checkboxes"]
    test_file = remote_temp_path("test_checkboxes") + ".md"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_case["content"],
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write file: {write_json}"

            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(test_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read file: {read_json}"
            assert read_json['output'].rstrip('\n').replace('\r\n', '\n') == test_case["content"].rstrip('\n'), "Content mismatch for checkboxes"

            logger.info("Checkboxes content verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_write_read_math_symbols(mcp_test_environment):
    """Test writing and reading files with mathematical symbols."""
    print_test_header("Testing file write/read with math symbols")
    logger.info("Starting math symbols file test")

    test_case = UNICODE_TEST_CASES["math_and_arrows"]
    test_file = remote_temp_path("test_math") + ".md"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_case["content"],
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write file: {write_json}"

            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(test_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read file: {read_json}"
            assert read_json['output'].rstrip('\n').replace('\r\n', '\n') == test_case["content"].rstrip('\n'), "Content mismatch for math symbols"

            logger.info("Math symbols content verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_write_read_box_drawing(mcp_test_environment):
    """Test writing and reading files with box drawing characters."""
    print_test_header("Testing file write/read with box drawing characters")
    logger.info("Starting box drawing file test")

    test_case = UNICODE_TEST_CASES["box_drawing"]
    test_file = remote_temp_path("test_boxes") + ".txt"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_case["content"],
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write file: {write_json}"

            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(test_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read file: {read_json}"
            assert read_json['output'].rstrip('\n').replace('\r\n', '\n') == test_case["content"].rstrip('\n'), "Content mismatch for box drawing"

            logger.info("Box drawing content verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_write_read_international(mcp_test_environment):
    """Test writing and reading files with international characters."""
    print_test_header("Testing file write/read with international text")
    logger.info("Starting international text file test")

    test_case = UNICODE_TEST_CASES["international_text"]
    test_file = remote_temp_path("test_international") + ".txt"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_case["content"],
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write file: {write_json}"

            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(test_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read file: {read_json}"
            assert read_json['output'].rstrip('\n').replace('\r\n', '\n') == test_case["content"].rstrip('\n'), "Content mismatch for international text"

            logger.info("International text content verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_write_read_mixed_heavy_unicode(mcp_test_environment):
    """Test writing and reading files with heavy mix of all Unicode types."""
    print_test_header("Testing file write/read with heavy mixed Unicode")
    logger.info("Starting mixed heavy Unicode file test")

    test_case = UNICODE_TEST_CASES["mixed_heavy_unicode"]
    test_file = remote_temp_path("test_mixed_unicode") + ".md"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_case["content"],
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write file: {write_json}"

            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(test_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read file: {read_json}"
            assert read_json['output'].rstrip('\n').replace('\r\n', '\n') == test_case["content"].rstrip('\n'), "Content mismatch for mixed Unicode"

            logger.info("Mixed heavy Unicode content verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_append_unicode(mcp_test_environment):
    """Test appending Unicode content to existing file."""
    print_test_header("Testing file append with Unicode")
    logger.info("Starting Unicode append test")

    test_file = remote_temp_path("test_append_unicode") + ".md"
    initial_content = "# Initial Content ✅\n\nSome text here.\n"
    append_content = "\n## Appended Section 🎉\n\n• New bullet point ★\n• Another point →\n"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Write initial content
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": initial_content,
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, "Failed to write initial content"

            # Append content
            append_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": append_content,
                "append": True
            })
            append_json = json.loads(extract_result_text(append_result))
            assert append_json.get('success') == True, "Failed to append content"

            # Read and verify
            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(test_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', "Failed to read file"

            expected_content = (initial_content + append_content).rstrip('\n')
            actual_content = read_json['output'].rstrip('\n').replace('\r\n', '\n')
            assert actual_content == expected_content, \
                f"Appended content mismatch!\nExpected:\n{expected_content}\n\nGot:\n{actual_content}"

            logger.info("Unicode append verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_line_replace_unicode(mcp_test_environment):
    """Test replacing lines containing Unicode characters."""
    print_test_header("Testing line replacement with Unicode")
    logger.info("Starting Unicode line replacement test")

    test_file = remote_temp_path("test_line_replace_unicode") + ".md"
    initial_content = """# Task List

[☐] Task 1: Initial task
[☐] Task 2: Another task
[☐] Task 3: Final task
"""

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Write initial content
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": initial_content,
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, "Failed to write initial content"

            # Replace line with Unicode characters
            replace_result = await client.call_tool("ssh_file_replace_line", {
                "file_path": test_file,
                "match_line": "[☐] Task 2: Another task",
                "new_line": "[☑] Task 2: Completed! ✅ 🎉"
            })
            replace_json = json.loads(extract_result_text(replace_result))
            assert replace_json.get('success') == True, f"Failed to replace line: {replace_json}"

            # Read and verify
            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(test_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            output = read_json['output'].rstrip('\n').replace('\r\n', '\n')

            assert "[☑] Task 2: Completed! ✅ 🎉" in output, \
                f"Replacement not found in content:\n{output}"
            assert "[☐] Task 1: Initial task" in output, "Original line 1 should still exist"
            assert "[☐] Task 3: Final task" in output, "Original line 3 should still exist"

            logger.info("Unicode line replacement verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_copy_unicode_content(mcp_test_environment):
    """Test copying files with Unicode content."""
    print_test_header("Testing file copy with Unicode content")
    logger.info("Starting Unicode file copy test")

    test_case = UNICODE_TEST_CASES["emojis"]
    source_file = remote_temp_path("test_copy_source") + ".md"
    dest_file = remote_temp_path("test_copy_dest") + ".md"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Write source file
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": source_file,
                "content": test_case["content"],
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, "Failed to write source file"

            # Copy file
            copy_result = await client.call_tool("ssh_file_copy", {
                "source_path": source_file,
                "destination_path": dest_file
            })
            copy_json = json.loads(extract_result_text(copy_result))
            assert copy_json.get('success') == True, f"Failed to copy file: {copy_json}"

            # Read destination and verify
            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(dest_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', "Failed to read destination file"
            assert read_json['output'].rstrip('\n').replace('\r\n', '\n') == test_case["content"].rstrip('\n'), "Copied content doesn't match original"

            logger.info("Unicode file copy verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(source_file), "io_timeout": 5.0})
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(dest_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_find_lines_unicode(mcp_test_environment):
    """Test finding lines containing Unicode patterns."""
    print_test_header("Testing find lines with Unicode patterns")
    logger.info("Starting Unicode find lines test")

    test_file = remote_temp_path("test_find_unicode") + ".md"
    content = """# Project Tasks

✅ Completed: Setup environment
✅ Completed: Write tests
❌ Failed: Deploy to production
⚠️ Warning: Memory usage high
✅ Completed: Code review
❌ Failed: Integration test
"""

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Write file
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": content,
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, "Failed to write file"

            # Find lines with ✅
            find_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file,
                "pattern": "✅"
            })
            find_json = json.loads(extract_result_text(find_result))
            # The tool returns total_matches and matches, not success
            assert 'matches' in find_json, f"Failed to find lines: {find_json}"

            # Should find 3 lines with ✅
            matches = find_json.get('matches', [])
            assert len(matches) == 3, f"Expected 3 matches for ✅, got {len(matches)}"

            # Find lines with ❌
            find_result2 = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file,
                "pattern": "❌"
            })
            find_json2 = json.loads(extract_result_text(find_result2))
            matches2 = find_json2.get('matches', [])
            assert len(matches2) == 2, f"Expected 2 matches for ❌, got {len(matches2)}"

            logger.info("Unicode find lines verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_file_write_with_sudo_unicode(mcp_test_environment):
    """Test writing Unicode content with sudo privileges (Linux only - uses sudo)."""
    print_test_header("Testing sudo file write with Unicode")
    logger.info("Starting sudo Unicode write test")

    test_file = "/tmp/test_sudo_unicode_" + str(int(time.time())) + ".md"
    content = """# System Configuration 🔧

## Settings ⚙️
• Option A: Enabled ✅
• Option B: Disabled ❌
• Option C: Warning ⚠️

© 2024 System Admin™
"""

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Write with sudo
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write file with sudo: {write_json}"

            # Read back (with sudo since we wrote as root)
            read_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 10.0,
                "use_sudo": True
            })
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read file: {read_json}"
            assert read_json['output'].rstrip('\n') == content.rstrip('\n'), "Content mismatch for sudo Unicode write"

            logger.info("Sudo Unicode write verified successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_file_command(test_file),
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
@pytest.mark.parametrize("test_name,test_case", list(UNICODE_TEST_CASES.items()))
async def test_file_unicode_parametrized(mcp_test_environment, test_name, test_case):
    """Parametrized test for all Unicode test cases."""
    print_test_header(f"Testing Unicode: {test_case['description']}")
    logger.info(f"Starting parametrized test: {test_name}")

    test_file = remote_temp_path(f"test_param_{test_name}") + ".txt"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Write
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_case["content"],
                            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json.get('success') == True, f"Failed to write {test_name}: {write_json}"

            # Read
            read_result = await client.call_tool("ssh_cmd_run", {"command": read_file_command(test_file), "io_timeout": 10.0})
            read_json = json.loads(extract_result_text(read_result))
            assert read_json.get('status') == 'success', f"Failed to read {test_name}: {read_json}"

            # Verify
            actual_content = read_json['output'].rstrip('\n').replace('\r\n', '\n')
            assert actual_content == test_case["content"].rstrip('\n'), \
                f"Content mismatch for {test_name}!\nExpected length: {len(test_case['content'])}\nGot length: {len(actual_content)}"

            logger.info(f"Test {test_name} passed successfully")

        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()
