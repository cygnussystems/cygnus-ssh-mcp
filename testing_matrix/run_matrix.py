#!/usr/bin/env python3
"""
Cross-Platform Test Matrix Runner

Runs the full test suite from remote runner machines against all target platforms.
Uses SSH MCP tools to orchestrate test execution on remote runners.

Usage:
    python testing_matrix/run_matrix.py                    # Run all 9 combinations
    python testing_matrix/run_matrix.py --runner=linux     # Run from Linux only
    python testing_matrix/run_matrix.py --target=windows   # Test against Windows only
    python testing_matrix/run_matrix.py --runner=macos --target=linux  # Single combo
    python testing_matrix/run_matrix.py --skip-build       # Use existing wheel
"""

import argparse
import asyncio
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

# Add project src to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src_path = os.path.join(project_root, 'src')
sys.path.insert(0, src_path)

from config import RUNNERS, TARGETS, MATRIX_WORKSPACE, TEST_DEPENDENCIES
import tempfile
from pathlib import Path
from cygnus_ssh_mcp import server as _server
from cygnus_ssh_mcp.host_manager import SshHostManager
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

# Own throwaway host config, like the test suites - never write the runner hosts (and
# their passwords) into the user's real ~/.mcp_ssh_hosts.toml
_server.host_manager = _server._default_host_manager = SshHostManager(
    config_path=Path(tempfile.mkdtemp(prefix="mcp_ssh_matrix_hosts_")) / "mcp_ssh_hosts.toml"
)

# The runners get the tests but not pyproject.toml - these are the pytest settings the
# suite depends on (session-wide asyncio loop, see [tool.pytest.ini_options])
# Same selection as the local full runs: the production sudo tests run separately
PYTEST_ARGS = ("-p no:cacheprovider -W ignore::DeprecationWarning -rfE "
               "--ignore=testing_mcp/test_tool__sudo_production.py")

PYTEST_INI = """[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = session
asyncio_default_test_loop_scope = session
"""

# Per-target limit for one full pytest run on a runner (a full Windows-target run takes
# ~70 min from this PC; slower runners need headroom)
# Full pytest output per combination (runner_to_target.txt), kept locally
RESULTS_DIR = os.path.join(tempfile.gettempdir(), "mcp_ssh_matrix_results")
os.makedirs(RESULTS_DIR, exist_ok=True)

TEST_RUNTIME_LIMIT = {'linux': 3600, 'macos': 3600, 'windows': 3 * 3600}


@dataclass
class TestResult:
    """Result of a single test run."""
    runner: str
    target: str
    passed: int
    failed: int
    errors: int
    skipped: int
    duration: float
    success: bool
    output: str = ""
    error_msg: str = ""


def extract_result_text(result) -> Optional[str]:
    """Extract text from MCP tool result."""
    if hasattr(result, 'content') and len(result.content) > 0:
        return result.content[0].text
    elif isinstance(result, list) and len(result) > 0:
        if hasattr(result[0], 'text'):
            return result[0].text
    return None


def parse_pytest_output(output: str) -> Dict:
    """Parse pytest output to extract test counts."""
    result = {'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0, 'success': False}

    # Look for summary line like "135 passed, 2 skipped in 180.23s"
    # or "1 failed, 134 passed, 5 warnings in 182.58s (0:03:02)"
    summary_match = re.search(
        r'=+\s*([\d\w\s,]+)\s+in\s+[\d.]+s.*?=+',
        output, re.IGNORECASE
    )

    if summary_match:
        summary = summary_match.group(1)

        passed = re.search(r'(\d+)\s+passed', summary)
        failed = re.search(r'(\d+)\s+failed', summary)
        errors = re.search(r'(\d+)\s+error', summary)
        skipped = re.search(r'(\d+)\s+skipped', summary)

        if passed:
            result['passed'] = int(passed.group(1))
        if failed:
            result['failed'] = int(failed.group(1))
        if errors:
            result['errors'] = int(errors.group(1))
        if skipped:
            result['skipped'] = int(skipped.group(1))

        result['success'] = result['failed'] == 0 and result['errors'] == 0

    return result


def build_wheel() -> str:
    """Build wheel in dist/ folder, return path to wheel file."""
    print("Building wheel...")

    # Clean old wheels
    dist_dir = os.path.join(project_root, 'dist')
    if os.path.exists(dist_dir):
        for f in glob.glob(os.path.join(dist_dir, '*.whl')):
            os.remove(f)

    # Build wheel
    result = subprocess.run(
        [sys.executable, '-m', 'build', '--wheel'],
        cwd=project_root,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"Build failed: {result.stderr}")
        sys.exit(1)

    # Find the wheel
    wheels = glob.glob(os.path.join(dist_dir, '*.whl'))
    if not wheels:
        print("No wheel found after build!")
        sys.exit(1)

    wheel_path = wheels[-1]  # Latest wheel
    print(f"Built: {os.path.basename(wheel_path)}")
    return wheel_path


def generate_env_content() -> str:
    """Generate .env file content with all target credentials."""
    lines = [
        "# Auto-generated for matrix testing",
        "",
        "# Linux Target",
        f"LINUX_SSH_HOST={TARGETS['linux']['host']}",
        f"LINUX_SSH_PORT={TARGETS['linux']['port']}",
        f"LINUX_SSH_USER={TARGETS['linux']['user']}",
        f"LINUX_SSH_PASSWORD={TARGETS['linux']['password']}",
        "",
        "# Windows Target",
        f"WINDOWS_SSH_HOST={TARGETS['windows']['host']}",
        f"WINDOWS_SSH_PORT={TARGETS['windows']['port']}",
        f"WINDOWS_SSH_USER={TARGETS['windows']['user']}",
        f"WINDOWS_SSH_PASSWORD={TARGETS['windows']['password']}",
        "",
        "# macOS Target",
        f"MACOS_SSH_HOST={TARGETS['macos']['host']}",
        f"MACOS_SSH_PORT={TARGETS['macos']['port']}",
        f"MACOS_SSH_USER={TARGETS['macos']['user']}",
        f"MACOS_SSH_PASSWORD={TARGETS['macos']['password']}",
        "",
    ]
    return '\n'.join(lines)


async def run_to_completion(client, command: str, runtime_timeout: float,
                            io_timeout: float = 60, tail_lines: int = 400) -> Dict:
    """Run a command and wait for its real end. Since 1.6.0 a single ssh_cmd_run call
    hands off after at most ~50s (status wait_timeout/io_timeout, command still
    running) - poll ssh_cmd_check_status until it finishes, then fetch the last
    `tail_lines` of output (enough for pytest's summary and failure list)."""
    result = json.loads(extract_result_text(await client.call_tool("ssh_cmd_run", {
        "command": command, "runtime_timeout": runtime_timeout, "io_timeout": io_timeout})))
    if result.get('status') not in ('wait_timeout', 'io_timeout') or not result.get('still_running'):
        output = result.get('output') if 'output' in result else \
            (result.get('stdout') or '') + (result.get('stderr') or '')
        return {'status': result.get('status'), 'exit_code': result.get('exit_code'),
                'output': output or '', 'raw': result}
    handle_id = result.get('handle_id') or result.get('id')
    while True:
        status = json.loads(extract_result_text(await client.call_tool(
            "ssh_cmd_check_status", {"handle_id": handle_id, "wait_seconds": 30})))
        if status.get('status') not in ('running', 'unknown_still_running'):
            break
    lines = []
    for stream in ('stdout', 'stderr'):
        # .data, not the text content: an empty stream comes back with no text content at all
        chunk = (await client.call_tool(
            "ssh_cmd_output", {"handle_id": handle_id, "lines": tail_lines, "stream": stream})).data
        if isinstance(chunk, list):
            lines.extend(chunk)
    output = ''.join(line if line.endswith('\n') else line + '\n' for line in lines)
    return {'status': status.get('status'), 'exit_code': status.get('exit_code'),
            'output': output, 'raw': status}


async def ensure_disconnected(client):
    """Ensure SSH is disconnected."""
    try:
        result = await client.call_tool("ssh_conn_is_connected", {})
        is_connected = json.loads(extract_result_text(result))
        if is_connected:
            await client.call_tool("ssh_host_disconnect", {})
    except Exception:
        pass


async def connect_to_runner(client, runner_name: str) -> bool:
    """Connect to a runner machine."""
    runner = RUNNERS[runner_name]

    # Add host config if needed
    try:
        await client.call_tool("ssh_conn_add_host", {
            "user": runner['user'],
            "host": runner['host'],
            "port": runner['port'],
            "password": runner['password'],
        })
    except Exception:
        pass  # Host might already exist

    # Connect
    host_key = f"{runner['user']}@{runner['host']}"
    result = await client.call_tool("ssh_conn_connect", {"host_name": host_key})
    result_json = json.loads(extract_result_text(result))

    return result_json.get('status') == 'success'


async def run_on_runner(client, runner_name: str, targets: List[str],
                        tests: str = 'testing_mcp/') -> List[TestResult]:
    """Run tests from a runner machine against specified targets."""
    runner = RUNNERS[runner_name]
    results = []

    print(f"\n{'='*60}")
    print(f"Runner: {runner_name.upper()} ({runner['user']}@{runner['host']})")
    print('='*60)

    # Disconnect any existing connection
    await ensure_disconnected(client)

    # Connect to runner
    if not await connect_to_runner(client, runner_name):
        print(f"  FAILED to connect to runner {runner_name}")
        for target in targets:
            results.append(TestResult(
                runner=runner_name, target=target,
                passed=0, failed=0, errors=1, skipped=0,
                duration=0, success=False,
                error_msg="Failed to connect to runner"
            ))
        return results

    # Setup workspace path
    sep = runner['path_sep']
    workspace = f"{runner['home']}{sep}{MATRIX_WORKSPACE}"

    # Platform-specific commands
    # Note: Windows SSH defaults to CMD, so we wrap PowerShell commands
    if runner_name == 'windows':
        cleanup_cmd = f'powershell -Command "Remove-Item -Recurse -Force \'{workspace}\' -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Path \'{workspace}\' -Force | Out-Null"'
    else:
        cleanup_cmd = f'rm -rf "{workspace}" && mkdir -p "{workspace}"'

    # 1. Cleanup previous installation
    print(f"  Cleaning workspace...")
    await client.call_tool("ssh_cmd_run", {
        "command": cleanup_cmd,
        "runtime_timeout": 60
    })

    # 2. Upload wheel
    print(f"  Uploading wheel...")
    dist_path = os.path.join(project_root, 'dist')
    await client.call_tool("ssh_dir_transfer", {
        "direction": "upload",
        "local_path": dist_path,
        "remote_path": f"{workspace}{sep}dist"
    })

    # 3. Upload test files
    print(f"  Uploading test files...")
    testing_path = os.path.join(project_root, 'testing_mcp')
    await client.call_tool("ssh_dir_transfer", {
        "direction": "upload",
        "local_path": testing_path,
        "remote_path": f"{workspace}{sep}testing_mcp"
    })

    # 4. Create venv and install
    print(f"  Creating venv and installing...")
    if runner_name == 'windows':
        # Quoted: an unquoted 'pytest-asyncio>=1.0' is a redirect to a file '=1.0' in
        # PowerShell and bash, and aborts the whole line in zsh (macOS)
        deps_str = ' '.join(f"'{d}'" for d in TEST_DEPENDENCIES)
        python_path = runner['python']
        # Windows: wrap in powershell -Command, use & for calling exe with spaces in path
        # Note: Don't use --quiet so pip produces output and doesn't hit io_timeout
        venv_cmd = f'powershell -Command "cd \'{workspace}\'; & \'{python_path}\' -m venv venv; .\\venv\\Scripts\\Activate.ps1; pip install (Get-ChildItem dist\\*.whl).FullName; pip install {deps_str}"'
    else:
        deps = ' '.join(shlex.quote(d) for d in TEST_DEPENDENCIES)  # see the Windows branch
        venv_cmd = f'''
cd "{workspace}"
{runner['python']} -m venv venv
source venv/bin/activate
pip install dist/*.whl
pip install {deps}
'''

    # Windows pip install may not produce regular output, so use a longer io_timeout
    io_timeout = 180 if runner_name == 'windows' else 60
    install = await run_to_completion(client, venv_cmd, runtime_timeout=1200, io_timeout=io_timeout)
    if install['exit_code'] not in (0, None) or install['status'] not in ('success', 'completed'):
        print(f"  Install FAILED ({install['status']}, exit {install['exit_code']}):\n{install['output'][-2000:]}")
        return [TestResult(runner=runner_name, target=t, passed=0, failed=0, errors=1, skipped=0,
                           duration=0, success=False, error_msg="venv/pip install failed")
                for t in targets]

    # 5. Create .env file
    print(f"  Creating .env file...")
    env_content = generate_env_content()
    await client.call_tool("ssh_file_write", {
        "file_path": f"{workspace}{sep}testing_mcp{sep}.env",
        "content": env_content
    })

    await client.call_tool("ssh_file_write", {
        "file_path": f"{workspace}{sep}pytest.ini",
        "content": PYTEST_INI
    })

    # 6. Run tests for each target
    for target in targets:
        print(f"  Testing -> {target.upper()}...", end=" ", flush=True)
        start_time = time.time()

        if runner_name == 'windows':
            # Windows: After activating venv, use 'python' not the system Python path
            test_cmd = f'powershell -Command "cd \'{workspace}\'; .\\venv\\Scripts\\Activate.ps1; $env:TEST_PLATFORM = \'{target}\'; python -m pytest {tests} {PYTEST_ARGS} 2>&1"'
        else:
            # After activating venv, use 'python' not the system Python path
            test_cmd = f'''
cd "{workspace}"
source venv/bin/activate
TEST_PLATFORM={target} python -m pytest {tests} {PYTEST_ARGS} 2>&1
'''

        run = await run_to_completion(client, test_cmd, runtime_timeout=TEST_RUNTIME_LIMIT[target],
                                      io_timeout=300)
        duration = time.time() - start_time
        cmd_output = run['output']

        parsed = parse_pytest_output(cmd_output)

        # Keep the full output: the summary only lists failing test names
        out_file = os.path.join(RESULTS_DIR, f"{runner_name}_to_{target}.txt")
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(cmd_output)

        test_result = TestResult(
            runner=runner_name,
            target=target,
            passed=parsed['passed'],
            failed=parsed['failed'],
            errors=parsed['errors'],
            skipped=parsed['skipped'],
            duration=duration,
            success=parsed['success'],
            output=cmd_output
        )
        results.append(test_result)

        if test_result.success:
            print(f"{test_result.passed} passed in {duration:.1f}s")
        else:
            print(f"FAILED ({test_result.failed} failed, {test_result.errors} errors)")

    # 7. Cleanup
    print(f"  Cleaning up...")
    if runner_name == 'windows':
        await client.call_tool("ssh_cmd_run", {
            "command": f'powershell -Command "Remove-Item -Recurse -Force \'{workspace}\' -ErrorAction SilentlyContinue"',
            "runtime_timeout": 60
        })
    else:
        await client.call_tool("ssh_cmd_run", {
            "command": f'rm -rf "{workspace}"',
            "runtime_timeout": 60
        })

    return results


def print_summary(all_results: List[TestResult], total_duration: float):
    """Print formatted summary of all test results."""
    print("\n")
    print("=" * 60)
    print("                  CROSS-PLATFORM TEST MATRIX")
    print("=" * 60)
    print()

    # Group by runner
    runners = {}
    for r in all_results:
        if r.runner not in runners:
            runners[r.runner] = []
        runners[r.runner].append(r)

    total_passed = 0
    total_combinations = len(all_results)
    passed_combinations = 0

    for runner_name in ['linux', 'windows', 'macos']:
        if runner_name not in runners:
            continue

        runner = RUNNERS[runner_name]
        print(f"Runner: {runner_name.upper()} ({runner['user']}@{runner['host']})")

        for i, r in enumerate(runners[runner_name]):
            prefix = "" if i < len(runners[runner_name]) - 1 else ""
            status = "" if r.success else ""

            if r.error_msg:
                print(f"  -> {r.target.upper():8} ... {r.error_msg} {status}")
            else:
                counts = f"{r.passed} passed, {r.failed} failed, {r.errors} errors, {r.skipped} skipped"
                print(f"  -> {r.target.upper():8} ... {counts} in {r.duration:.0f}s {status}")
                # which tests failed (pytest's -rfE short summary lines)
                for line in r.output.splitlines():
                    if line.startswith(('FAILED ', 'ERROR ')):
                        print(f"       {line[:220]}")

            if r.success:
                passed_combinations += 1
            total_passed += r.passed

        print()

    print("=" * 60)
    print("                          SUMMARY")
    print("=" * 60)
    print(f"Combinations: {passed_combinations}/{total_combinations} passed")
    print(f"Total tests:  {total_passed}")
    print(f"Total time:   {total_duration/60:.1f}m")
    print("=" * 60)

    return passed_combinations == total_combinations


async def main():
    parser = argparse.ArgumentParser(description='Run cross-platform test matrix')
    parser.add_argument('--runner', choices=['linux', 'windows', 'macos', 'all'],
                       default='all', help='Runner machine(s) to use')
    parser.add_argument('--target', choices=['linux', 'windows', 'macos', 'all'],
                       default='all', help='Target platform(s) to test')
    parser.add_argument('--skip-build', action='store_true',
                       help='Skip wheel build, use existing')
    parser.add_argument('--wheel', help='Path to specific wheel file to use')
    parser.add_argument('--tests', default='testing_mcp/',
                        help='Test path(s) to run on the runners, relative to the workspace '
                             '(default: testing_mcp/ - the whole suite)')
    args = parser.parse_args()

    # Determine runners and targets
    if args.runner == 'all':
        runners = ['linux', 'windows', 'macos']
    else:
        runners = [args.runner]

    if args.target == 'all':
        targets = ['linux', 'windows', 'macos']
    else:
        targets = [args.target]

    print(f"\nTest Matrix: {len(runners)} runner(s) x {len(targets)} target(s)")
    print(f"Runners: {', '.join(runners)}")
    print(f"Targets: {', '.join(targets)}")

    # Build wheel if needed
    if not args.skip_build:
        wheel_path = build_wheel()
    else:
        wheels = glob.glob(os.path.join(project_root, 'dist', '*.whl'))
        if not wheels:
            print("No wheel found! Run without --skip-build first.")
            sys.exit(1)
        wheel_path = wheels[-1]
        print(f"Using existing wheel: {os.path.basename(wheel_path)}")

    # Run tests
    all_results = []
    start_time = time.time()

    async with Client(mcp) as client:
        for runner_name in runners:
            results = await run_on_runner(client, runner_name, targets, args.tests)
            all_results.extend(results)

        # Disconnect at end
        await ensure_disconnected(client)

    total_duration = time.time() - start_time

    # Print summary
    success = print_summary(all_results, total_duration)
    print(f"Full pytest output per combination: {RESULTS_DIR}")

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    asyncio.run(main())
