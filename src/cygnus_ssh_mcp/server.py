import logging
import sys
import os
import argparse
import asyncio
import tempfile
import shlex
import time
import functools
import re
import inspect
import itertools
import threading
from collections import OrderedDict
from pathlib import Path

# Allow running directly from source without pip install
_src_dir = Path(__file__).resolve().parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from fastmcp import FastMCP
from pydantic import Field, BaseModel
from typing import Annotated, Optional, Literal, Dict, Any, List, Union
from datetime import datetime, UTC
from cygnus_ssh_mcp.client import SshClient
from cygnus_ssh_mcp.models import SshError, CommandTimeout, CommandRuntimeTimeout, CommandFailed, SudoRequired, BusyError, CwdNotFound, OutputLimits, OperationProgress, set_current_progress, report_progress
from cygnus_ssh_mcp.ps_encode import powershell_encoded_command
from cygnus_ssh_mcp.ops.capability_gate import describe_capabilities
import stat as stat_module
import errno

from cygnus_ssh_mcp.host_manager import SshHostManager


def parse_args():
    parser = argparse.ArgumentParser(description="SSH MCP Server")
    parser.add_argument(
        '--config',
        type=str,
        help="Path to SSH hosts configuration file (TOML format)",
        default=None
    )
    parser.add_argument(
        '--max-wait',
        type=float,
        help=("Max seconds a single ssh_cmd_run call blocks before handing off with "
              "status='wait_timeout' (default: $MCP_SSH_MAX_WAIT or "
              f"{DEFAULT_MAX_FOREGROUND_WAIT:g}; 0 disables the cap)"),
        default=None
    )
    parser.add_argument(
        '--max-output', type=int, default=None,
        help="Output kept in memory per command and stream, in bytes; the earliest lines "
             "beyond it are dropped (default: $MCP_SSH_MAX_OUTPUT or 2097152 = 2 MB)")
    parser.add_argument(
        '--inline-output', type=int, default=None,
        help="Most recent output returned inline per stream by ssh_cmd_run, in bytes; the "
             "rest can be paged with ssh_cmd_output (default: $MCP_SSH_INLINE_OUTPUT or 32768)")
    parser.add_argument(
        '--output-memory', type=int, default=None,
        help="Total output memory across the command history, in bytes (default: "
             "$MCP_SSH_OUTPUT_MEMORY or 52428800 = 50 MB)")
    return parser.parse_args()


def _apply_output_limits(args):
    """Set models.OutputLimits from CLI args, falling back to MCP_SSH_* env vars."""
    for attr, arg, env in (('per_stream', args.max_output, 'MCP_SSH_MAX_OUTPUT'),
                           ('inline', args.inline_output, 'MCP_SSH_INLINE_OUTPUT'),
                           ('total', args.output_memory, 'MCP_SSH_OUTPUT_MEMORY')):
        value = arg
        if value is None and os.environ.get(env):
            try:
                value = int(os.environ[env])
            except ValueError:
                logging.getLogger("SSH_MCP_Server").warning(f"Ignoring invalid {env}={os.environ[env]!r}")
        if value is not None and value > 0:
            setattr(OutputLimits, attr, value)


# Many MCP clients (e.g. anything on the TypeScript SDK's default) abort a tool call
# after 60s with "Request timed out" - the caller then never sees the id/pid handoff.
# So ssh_cmd_run never blocks longer than this, whatever io_timeout/wait_timeout ask
# for; the command keeps running and is handed off exactly like a wait_timeout.
DEFAULT_MAX_FOREGROUND_WAIT = 50.0


def _max_wait_from_env() -> Optional[float]:
    """Read the foreground-wait cap from MCP_SSH_MAX_WAIT (0 or negative = no cap)."""
    raw = os.environ.get('MCP_SSH_MAX_WAIT')
    if raw is None:
        return DEFAULT_MAX_FOREGROUND_WAIT
    try:
        value = float(raw)
    except ValueError:
        logging.getLogger("SSH_MCP_Server").warning(
            f"Ignoring invalid MCP_SSH_MAX_WAIT={raw!r}, using {DEFAULT_MAX_FOREGROUND_WAIT:g}s")
        return DEFAULT_MAX_FOREGROUND_WAIT
    return value if value > 0 else None


# None = no cap. Overridden by --max-wait in main().
max_foreground_wait: Optional[float] = _max_wait_from_env()



# Initialize host manager with default config
# This will be re-initialized with CLI args if main() is called
host_manager = SshHostManager()

# The "default" host manager for the running server - what ssh_host_use_config()
# reverts to. Kept in sync with `host_manager` whenever the default itself changes
# (i.e. in main(), if --config was passed), but NOT when ssh_host_use_config()
# points `host_manager` at an ad-hoc alternate file for the rest of the session.
_default_host_manager = host_manager


# ===================
# Logging Setup
# ===================

# Create main logger
logger = logging.getLogger("SSH_MCP_Server")

def setup_logging():
    """Configure basic logging for the MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stderr)]
    )
    logger.info("Logging configured")

# Initialize logging early
setup_logging()


# ===================
# MCP Server Instance
# ===================

# Create the main MCP server instance
try:
    mcp = FastMCP(
        name="SSH_Management_Server"
    )
    # Initialize ssh_client as a member variable
    mcp.ssh_client = None
    logger.info(f"Created MCP server instance '{mcp.name}'")
except Exception as e:
    logger.critical(f"Failed to create MCP instance: {e}", exc_info=True)
    sys.exit(1)


# ===================
# Global State
# ===================

# The SSH client will be an instance variable of the MCP server


# ===================
# Cleanup Handlers
# ===================

# Cleanup function - will be called manually at shutdown
async def cleanup_ssh():
    """Clean up SSH connection when server shuts down."""
    if mcp.ssh_client:
        logger.info("Closing SSH connection on shutdown")
        try:
            mcp.ssh_client.close()
        except Exception as e:
            logger.error(f"Error closing SSH connection: {e}")
        finally:
            mcp.ssh_client = None
    logger.info("SSH cleanup complete")

# Register shutdown handler if the FastMCP version supports it
try:
    mcp.on_shutdown(cleanup_ssh)
    logger.info("Registered shutdown handler")
except AttributeError:
    logger.info("FastMCP version doesn't support on_shutdown, will clean up manually")


def _connection_metadata() -> dict:
    """
    Cheap {host, alias, user, cwd} block identifying which connection a mutating
    tool actually ran against. Added to every mutating tool's response after a
    real incident where a file was written to the wrong host in a multi-host
    session with nothing in the response to catch it.
    """
    if not mcp.ssh_client:
        return {'host': None, 'alias': None, 'user': None, 'cwd': None}
    status = mcp.ssh_client.get_connection_status()
    return {
        'host': mcp.ssh_client.host,
        'alias': mcp.ssh_client.alias,
        'user': status.get('user'),
        'cwd': status.get('cwd')
    }


# ====================================================
#          Core SSH Tools
# ====================================================


# Add this within your mcp_ssh_server.py file, similar to other tools

# ===================
# Long operations: worker threads, one foreground operation, 50s handoff
# ===================
#
# Every tool that works on the remote host runs in a worker thread, so the event loop
# (and with it ssh_conn_is_connected, ssh_cmd_history, ssh_cmd_check_status, ...) always
# stays responsive. Before this, only ssh_cmd_run did: an archive/transfer/search tool
# blocked the whole server for as long as it ran (archive ops allow up to 30 minutes),
# and a client that gave up at 60s then saw every later call time out too.
#
# - Operation tools (@operation_tool): one at a time. A second one - or an ssh_cmd_run -
#   while one is running fails fast with a "busy" error naming the running operation and
#   its handle, instead of queueing (a queued call would just time out at the client).
# - If an operation hasn't finished within max_foreground_wait (50s), the call returns
#   {status: 'in_progress', handle_id, ...}. The operation keeps running; its real result
#   or error is collected with ssh_cmd_check_status(handle_id=...).
# - Control/status tools (@threaded_tool: task status/kill, command kill) also run in a
#   worker thread but never wait for the operation lock, so they always work.

# ===================
# Lost connections
# ===================
#
# A connection can die without paramiko noticing (e.g. dropped by a firewall after a long
# idle - seen 2026-09-27: ssh_conn_is_connected said true while the next call failed with
# "[WinError 10054] An existing connection was forcibly closed"). When a call fails with a
# connection-level error, the link is checked; if it's really gone, the connection is
# dropped and the caller gets one clear CONNECTION_LOST message with the next step.

_CONNECTION_LOST_MARKERS = (
    'ssh session not active', 'socket is closed', 'connection reset', 'forcibly closed',
    'broken pipe', 'connection aborted', 'server connection dropped', 'eof during negotiation',
    'winerror 10054', 'winerror 10053', 'winerror 10060', 'connection timed out',
    'no existing session', 'transport is not active',
)
_last_connection_loss = None   # human-readable reason, reported by later not-connected errors


def _looks_like_connection_loss(exc):
    """True if an exception (or anything in its cause chain) is a connection failure."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (ConnectionError, EOFError, BrokenPipeError)):
            return True
        text = str(exc).lower()
        if any(marker in text for marker in _CONNECTION_LOST_MARKERS):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _not_connected_message():
    message = ("No active SSH connection. Connect (or reconnect) with ssh_conn_connect(host_name=...) - see ssh_host_list for configured hosts.")
    if _last_connection_loss:
        message += f" (The previous connection was {_last_connection_loss}.)"
    return message


def _connection_label():
    """The configured alias, else user@host - from local state only (no remote call:
    this runs right after a connection failure)."""
    client = mcp.ssh_client
    if client is None:
        return "the host"
    return getattr(client, 'alias', None) or f"{client.user}@{client.host}"


def _connection_lost_message(label, reason):
    return (f"CONNECTION_LOST: the SSH connection to {label} is gone ({reason}). Reconnect "
            f"with ssh_conn_connect(host_name='{label}'). If the call that failed could have "
            f"changed something on the host, check whether it took effect before running it "
            f"again - it may have completed, partly completed, or not run at all.")


def _handle_possible_connection_loss(exc):
    """If `exc` is a connection failure and the link is really dead, drop the connection
    and return a CONNECTION_LOST SshError to raise instead; otherwise return None."""
    global _last_connection_loss
    if not _looks_like_connection_loss(exc):
        return None
    client = mcp.ssh_client
    if client is not None and client.probe_alive():
        return None  # a one-off failure; the connection itself is fine
    label = _connection_label()
    reason = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
    _last_connection_loss = f"lost at {datetime.now(UTC).isoformat()}: {reason}"
    logger.warning(f"Connection to {label} lost: {reason}")
    if client is not None and mcp.ssh_client is client:
        try:
            client.close()
        except Exception:
            pass
        mcp.ssh_client = None
    return SshError(_connection_lost_message(label, reason))


def _check_result_for_connection_loss(result):
    """Many tools catch errors themselves and RETURN them ({'error': ...} or
    {'message': ...}) rather than raising. Give those the same CONNECTION_LOST treatment:
    same response shape, but the error text replaced and error_type='connection_lost'."""
    if not isinstance(result, dict):
        return result
    for key in ('error', 'message'):
        text = result.get(key)
        if isinstance(text, str) and text and _looks_like_connection_loss(SshError(text)):
            lost = _handle_possible_connection_loss(SshError(text))
            if lost is not None:
                # Keep only what identifies the request; drop result fields that would
                # now be misleading (e.g. ssh_file_stat's 'exists': False)
                kept = {k: v for k, v in result.items() if k in _REQUEST_KEYS}
                return {**kept, 'status': 'error', 'success': False, 'error': str(lost),
                        'error_type': 'connection_lost'}
    return result


_REQUEST_KEYS = ('path', 'file_path', 'dir_path', 'source_path', 'destination_path',
                 'archive_path', 'local_path', 'remote_path', 'command', 'pattern', 'direction')


class _Operation:
    """A long-running tool call that may outlive the request that started it."""

    def __init__(self, op_id, tool, summary):
        self.id = op_id
        self.tool = tool
        self.summary = summary
        self.start_ts = datetime.now(UTC)
        self.end_ts = None
        self.done = threading.Event()
        self.result = None
        self.error = None
        self.progress = OperationProgress()

    def status(self):
        if not self.done.is_set():
            return 'running'
        return 'failed' if self.error is not None else 'completed'

    def history_entry(self):
        return {
            'id': self.id,
            'cmd': f"[{self.tool}] {self.summary}",
            'exit_code': None if not self.done.is_set() else (1 if self.error is not None else 0),
            'start_ts': self.start_ts.isoformat(),
            'end_ts': self.end_ts.isoformat() if self.end_ts else None,
            'pid': None,
            'origin': 'operation',
            'parent_tool': self.tool,
        }


# Operation handle IDs start high so they never collide with per-connection command IDs.
_operation_ids = itertools.count(1_000_001)
_operations = OrderedDict()   # id -> _Operation (handed-off or still running)
_MAX_OPERATIONS_KEPT = 50
_foreground_lock = threading.Lock()
_foreground_op = None         # the _Operation holding _foreground_lock, if any

_SECRET_ARG_NAMES = {'password', 'sudo_password', 'key_passphrase', 'content'}


def _summarize_args(kwargs):
    parts = []
    for name, value in kwargs.items():
        if value is None or name in _SECRET_ARG_NAMES:
            continue
        text = repr(value)
        parts.append(f"{name}={text[:80] + '...' if len(text) > 80 else text}")
    summary = ", ".join(parts)
    return summary[:300]


def _busy_message():
    op = _foreground_op
    if op is None:
        return ("busy: another operation is still running on this server. Wait a moment, "
                "then retry.")
    if op.id is None:  # a foreground ssh_cmd_run (see ssh_cmd_run)
        wait = f"within {max_foreground_wait:g}s" if max_foreground_wait else "when it finishes"
        return (f"busy: ssh_cmd_run ({op.summary}) is still waiting in the foreground, started "
                f"{op.start_ts.isoformat()}. It returns {wait} (handing off if still running); "
                f"retry after that. Status tools keep working meanwhile.")
    return (f"busy: {op.tool} ({op.summary}) is still running as handle_id={op.id}, started "
            f"{op.start_ts.isoformat()}. Only one operation runs at a time. Poll it with "
            f"ssh_cmd_check_status(handle_id={op.id}) and retry after it finishes - do not "
            f"start it again. Status tools (ssh_cmd_check_status, ssh_cmd_history, "
            f"ssh_task_status, ssh_conn_is_connected) keep working meanwhile.")


def _remember_operation(op):
    _operations[op.id] = op
    while len(_operations) > _MAX_OPERATIONS_KEPT:
        oldest_id = next(iter(_operations))
        if not _operations[oldest_id].done.is_set():
            break
        _operations.pop(oldest_id)


# Appended to every @operation_tool description, so a model reading the tool list knows
# up front that a long call can come back as a handle instead of the result.
_HANDOFF_NOTE = """

    Long-running calls: if this takes longer than the server's wait cap (default 50s), it
    returns {"status": "in_progress", "handle_id": ...} instead of the result - the work keeps
    running. Get the result by polling ssh_cmd_check_status(handle_id=...), which returns this
    tool's normal response once finished. Do not call it again meanwhile. Only one such
    operation runs at a time; another one started meanwhile fails with a 'busy' error.
"""


def _doc_with_handoff_note(doc):
    """Add _HANDOFF_NOTE where tool descriptions keep it: before an 'Args:' section if
    there is one (FastMCP drops everything from 'Args:' on), else at the end."""
    match = re.search(r"^[ \t]*Args:[ \t]*$", doc, flags=re.MULTILINE)
    if match:
        return doc[:match.start()].rstrip() + _HANDOFF_NOTE + "\n" + doc[match.start():]
    return doc.rstrip() + _HANDOFF_NOTE


def _with_dict_result(func, wrapper):
    """An in_progress response is a dict: tools declared to return a list may now also
    return a dict (MCP clients validate results against the declared output schema)."""
    sig = inspect.signature(func)
    ret = sig.return_annotation
    if ret is not inspect.Signature.empty and ret is not dict:
        new_ret = Union[ret, dict]
        wrapper.__signature__ = sig.replace(return_annotation=new_ret)
        wrapper.__annotations__ = {**getattr(func, '__annotations__', {}), 'return': new_ret}


def operation_tool(func):
    """Run a remote tool in a worker thread, one operation at a time, handing off to
    ssh_cmd_check_status if it takes longer than max_foreground_wait (see above)."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        global _foreground_op
        if not _foreground_lock.acquire(blocking=False):
            raise SshError(_busy_message())
        op = _Operation(next(_operation_ids), func.__name__, _summarize_args(kwargs))
        _foreground_op = op
        # Registered up front, so a result is still discoverable (ssh_cmd_history /
        # ssh_cmd_check_status) even if the client abandons this request early.
        _remember_operation(op)

        def work():
            global _foreground_op
            set_current_progress(op.progress)
            try:
                op.result = _check_result_for_connection_loss(asyncio.run(func(*args, **kwargs)))
            except BaseException as e:  # noqa: BLE001 - reported via the operation
                op.error = _handle_possible_connection_loss(e) or e
            finally:
                set_current_progress(None)
                op.end_ts = datetime.now(UTC)
                _foreground_op = None
                _foreground_lock.release()
                op.done.set()

        threading.Thread(target=work, name=f"mcp-op-{op.id}", daemon=True).start()
        finished = await asyncio.to_thread(op.done.wait, max_foreground_wait)
        if finished:
            _operations.pop(op.id, None)  # delivered directly - no handle needed
            if op.error is not None:
                raise op.error
            return op.result
        return {
            'status': 'in_progress',
            'handle_id': op.id,
            'tool': op.tool,
            'operation': op.summary,
            'started': op.start_ts.isoformat(),
            'waited_seconds': max_foreground_wait,
            **({'progress': op.progress.snapshot()} if op.progress.snapshot() else {}),
            'next_step': (
                f"{op.tool} is still running on the server (it was NOT cancelled). Poll "
                f"ssh_cmd_check_status(handle_id={op.id}) - it returns this call's full result "
                f"(or its error) once finished. Do not start it again. Other status tools keep "
                f"working meanwhile; other operations are refused until it finishes."
            ),
        }

    _with_dict_result(func, wrapper)
    wrapper.__doc__ = _doc_with_handoff_note(func.__doc__ or '')
    return wrapper


def threaded_tool(func):
    """Run a quick remote status/control tool in a worker thread - never blocked by a
    running operation, and never blocking the event loop."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        def work():
            try:
                return _check_result_for_connection_loss(asyncio.run(func(*args, **kwargs)))
            except Exception as e:
                raise (_handle_possible_connection_loss(e) or e)
        return await asyncio.to_thread(work)

    return wrapper


def _operation_status_response(op, waited):
    response = {
        'handle_id': op.id,
        'waited_seconds': waited,
        'status': op.status(),
        'tool': op.tool,
        'operation': op.summary,
        'started': op.start_ts.isoformat(),
        'ended': op.end_ts.isoformat() if op.end_ts else None,
        'timestamp': datetime.now(UTC).isoformat(),
    }
    if response['status'] == 'running':
        progress = op.progress.snapshot()
        if progress:
            response['progress'] = progress
        response['elapsed_seconds'] = round((datetime.now(UTC) - op.start_ts).total_seconds(), 1)
        response['next_step'] = (f"Still running. Call ssh_cmd_check_status(handle_id={op.id}) "
                                 f"again to keep polling. Do not start it again. 'progress' shows "
                                 f"the current stage and, for transfers, bytes done; if its "
                                 f"last_update stops advancing for a long time, the operation may "
                                 f"be stuck.")
    elif response['status'] == 'completed':
        response['result'] = op.result
    else:
        response['error'] = str(op.error)
        response['error_type'] = type(op.error).__name__
    return response


@mcp.tool()
async def list_tools() -> list:
    """
    Retrieves a list of all available tools on this MCP server,
    along with their descriptions.

    Returns:
        A list of dictionaries, where each dictionary contains the 'name'
        and 'description' of an available tool.
    """
    logger.info("Request received to list available tools.")
    available_tools = []
    # FastMCP renamed this between major versions with no overlap: 2.x only has
    # get_tools() (returns a dict of {tool_name: FunctionTool}), 3.x only has
    # list_tools() (returns a list of FunctionTool, name read off each one) -
    # verified live 2026-07-05 against fastmcp 2.13.0.2 and 3.4.2. pyproject.toml's
    # fastmcp constraint is unbounded (>=2.0.0), so support both rather than
    # pinning to one side.
    try:
        if hasattr(mcp, 'list_tools'):
            tools = await mcp.list_tools()
            for tool_spec in tools:
                available_tools.append({
                    "name": tool_spec.name,
                    "description": getattr(tool_spec, 'description', 'No description available.')
                })
        else:
            tools_dict = await mcp.get_tools()
            for name, tool_spec in tools_dict.items():
                available_tools.append({
                    "name": name,
                    "description": getattr(tool_spec, 'description', 'No description available.')
                })
    except Exception as e:
        logger.error(f"Error listing tools: {e}", exc_info=True)

    return available_tools


@mcp.tool()
async def ssh_conn_is_connected() -> bool:
    """
    Check whether there is a WORKING SSH connection - a real round trip to the host (a
    few milliseconds normally, at most ~5s), not just a cached flag. A connection that
    died while idle (e.g. dropped by a firewall overnight) is detected, dropped, and
    reported as False.

    Returns:
        bool: True if the connection works, False otherwise - then reconnect with
        ssh_conn_connect(host_name=...).
    """
    global _last_connection_loss
    client = mcp.ssh_client
    if client is None:
        return False
    if await asyncio.to_thread(client.probe_alive):
        return True
    label = _connection_label()
    _last_connection_loss = f"lost at {datetime.now(UTC).isoformat()}: liveness check failed"
    logger.warning(f"Connection to {label} failed its liveness check - dropping it")
    if mcp.ssh_client is client:
        try:
            client.close()
        except Exception:
            pass
        mcp.ssh_client = None
    return False


@mcp.tool()
@operation_tool
async def ssh_conn_connect(
    host_name: Annotated[str, Field(description="The 'user@hostname' identifier or alias of a pre-configured host")]
) -> dict:
    """
    Establish an SSH connection using a pre-configured host.
    The host can be specified by its 'user@hostname' key or by its alias.

    Beyond the three fully-supported platforms (Linux, macOS, Windows), any
    other SSH target that responds to a basic shell command connects too,
    reported as os_type='flex' (e.g. FreeBSD/OPNsense/pfSense-style routers,
    or other unrecognized POSIX kernels). For 'linux' and 'flex' connections,
    a one-time capability probe checks the specific shell/coreutils features
    this server depends on (some embedded/BusyBox-based devices lack GNU
    extensions like `find -printf` or `tar --strip-components`) - the result
    is returned as 'capabilities' (raw probe results) and, if anything's
    missing, 'capability_warnings' (plain-English gaps). Tools that need a
    missing capability fail with a clear error naming what's missing, rather
    than a cryptic remote command failure - nothing is silently degraded.

    Returns:
        Dictionary with connection status and detailed system information.
        'capabilities'/'capability_warnings' are included for 'linux'/'flex'
        connections only (empty/omitted for macOS/Windows, which are already
        fully supported and not probed).
        Two different "os_version" fields: `connection.os_version` is a short
        platform/distro identifier used internally (e.g. 'debian', 'centos',
        'windows_server_2016', or 'unknown_linux'/'unknown_windows' if not recognized;
        None on macOS), while `system.os_version` is the human-readable version string
        reported by the OS itself (e.g. '12 (bookworm)', '14.8.9').
    """
    try:
        # Try to resolve the host by key or alias
        resolved_key, host_config = host_manager.resolve_host(host_name)
            
        if mcp.ssh_client:
            logger.warning("Closing existing SSH connection")
            mcp.ssh_client.close()
            
        # Expand ~ in keyfile path if present
        keyfile = host_config.get('keyfile')
        if keyfile:
            keyfile = os.path.expanduser(keyfile)

        mcp.ssh_client = SshClient(
            host=host_config['parsed_host'],
            user=host_config['parsed_user'],
            password=host_config.get('password'),
            keyfile=keyfile,
            key_passphrase=host_config.get('key_passphrase'),
            port=host_config['port'],
            sudo_password=host_config.get('sudo_password') or host_config.get('password')
        )
        mcp.ssh_client.alias = host_config.get('alias')

        # Get current working directory (use OS-appropriate command)
        if mcp.ssh_client.os_type == 'windows':
            cwd_result = mcp.ssh_client.run("cd", origin='connection_probe', parent_tool='ssh_conn_connect')
        else:
            cwd_result = mcp.ssh_client.run("pwd", origin='connection_probe', parent_tool='ssh_conn_connect')
        cwd = cwd_result.get_full_output().strip() if cwd_result.exit_code == 0 else "Unknown"

        # Update the connection status with the current working directory
        mcp.ssh_client.update_connection_status(force=True, parent_tool='ssh_conn_connect')

        # Get detailed system information
        status = mcp.ssh_client.get_connection_status(parent_tool='ssh_conn_connect')
        # Update the cwd in the connection status
        status['cwd'] = cwd
        system_info = mcp.ssh_client.full_status(parent_tool='ssh_conn_connect')
        
        result = {
            'status': 'success',
            'connected_to': resolved_key,  # The actual user@host key
            'host': host_config['parsed_host'],
            'user': host_config['parsed_user'],
            'port': host_config['port'],
            'current_directory': cwd,
            'os_type': status.get('os_type', 'Unknown'),
            'connection': status,
            'system': system_info
        }

        # Add elevation note for Windows
        if status.get('os_type') == 'windows':
            is_elevated = getattr(mcp.ssh_client, '_is_elevated', False)
            result['elevation_note'] = (
                "Windows elevation: use_sudo=True requires an Administrator session. "
                "Unlike Linux/macOS, Windows cannot elevate on-demand. "
                f"Current session is {'elevated (Administrator)' if is_elevated else 'NOT elevated (standard user)'}."
            )

        # Report probed capabilities for 'linux'/'flex' targets, so gaps are
        # visible upfront instead of discovered by trial and error - see
        # SshClient._probe_capabilities. Empty for macOS/Windows (never probed).
        if mcp.ssh_client.capabilities:
            result['capabilities'] = mcp.ssh_client.capabilities
            warnings = describe_capabilities(mcp.ssh_client.capabilities)
            if warnings:
                result['capability_warnings'] = warnings
        if status.get('os_type') == 'flex':
            result['flex_note'] = (
                f"This host reports an unrecognized kernel ('{mcp.ssh_client.os_subtype}') - "
                "not Linux, macOS, or Windows. Support is best-effort: Linux-style shell "
                "commands are used, gated by the 'capabilities' probe above, so unsupported "
                "operations fail with a clear error rather than a cryptic remote failure."
            )
        # Include alias info if the connection was made via alias
        if host_config.get('alias'):
            result['alias'] = host_config['alias']
        if host_name != resolved_key:
            result['resolved_from'] = host_name  # Show what alias was used
        return result
    except Exception as e:
        logger.error(f"Failed to connect to {host_name}: {e}")
        raise


@mcp.tool()
async def ssh_conn_add_host(
    user: Annotated[str, Field(description="Username for authentication")],
    host: Annotated[str, Field(description="Hostname or IP address")],
    password: Annotated[Optional[str], Field(description="Password for authentication", secret=True)] = None,
    port: Annotated[int, Field(description="SSH port", ge=1, le=65535)] = 22,
    sudo_password: Annotated[Optional[str], Field(description="Password for sudo operations (defaults to regular password if not provided)", secret=True)] = None,
    alias: Annotated[Optional[str], Field(description="Short name for easy connection (e.g., 'prod', 'staging')")] = None,
    description: Annotated[Optional[str], Field(description="Description of what this host is for")] = None,
    keyfile: Annotated[Optional[str], Field(description="Path to SSH private key file (e.g., ~/.ssh/id_rsa)")] = None,
    key_passphrase: Annotated[Optional[str], Field(description="Passphrase for encrypted SSH key", secret=True)] = None
) -> dict:
    """
    Add a new host configuration to the host configuration TOML file. Despite the name,
    this does NOT update an existing entry - if the `user@host` key already exists,
    this returns an error response (`{'status': 'error', ...}`, not a raised
    exception) rather than overwriting it; use ssh_host_update for that instead.

    Before calling this, check 'ssh_host_list' - the host you want may already be
    configured, in which case you can call 'ssh_conn_connect' directly without adding
    anything.

    Authentication requires either a password OR a keyfile (or both):
    - Password authentication: Provide `password`
    - Key-based authentication: Provide `keyfile` (and optionally `key_passphrase` if the key is encrypted)

    If using key-only authentication and sudo operations are needed, you must explicitly provide
    `sudo_password` unless the server has passwordless sudo configured.

    Warn the user that credentials will be visible to the LLM and that it would be better
    for the user to add the host directly in the host configuration file. That said,
    never read this file yourself (directly or via any file/shell tool) to look up,
    verify, or copy existing hosts' credentials - ssh_host_list, ssh_conn_add_host,
    ssh_host_update, ssh_host_remove, and ssh_host_use_config are the only tools you
    should need for host management, and none of them ever expose a stored
    password/passphrase back to you. This tool always adds to whichever config file
    is currently active (the server's default unless ssh_host_use_config was called
    to switch to an alternate one - check ssh_host_list's `config_path` if unsure).

    The host config file is `~/.mcp_ssh_hosts.toml` if it exists, otherwise
    `./mcp_ssh_hosts.toml` in the server's working directory - it stores every host's
    password, sudo password, and key passphrase in plaintext, which is exactly why the
    tools above exist instead of editing it by hand. The configuration is stored under
    a ["user@host"] key.

    Optional fields:
    - alias: A short name for connecting (e.g., 'prod' instead of 'deploy@production.example.com')
    - description: A text description of what the host is for

    Returns:
        On success: `{'status': 'success', 'message', 'key', 'host', 'user', 'port',
        'auth_method' ('key' or 'password'), and 'alias'/'description'/'keyfile' if
        provided}`.
        On failure (missing auth, duplicate host key, or duplicate alias):
        `{'status': 'error', 'error': <message>}` - for a duplicate host key, also
        includes `'existing_config'` (the current host/user/port/alias/description)
        so you can decide whether to use it as-is via `ssh_conn_connect` instead.
    """
    try:
        # Validate that at least one authentication method is provided
        if not password and not keyfile:
            return {
                'status': 'error',
                'error': 'Either password or keyfile must be provided for authentication'
            }

        key = f"{user}@{host}"
        existing = host_manager.get_host(key)
        if existing:
            return {
                'status': 'error',
                'error': f"Host {key} already exists in config",
                'existing_config': {
                    'host': existing['parsed_host'],
                    'user': existing['parsed_user'],
                    'port': existing['port'],
                    'alias': existing.get('alias'),
                    'description': existing.get('description')
                }
            }

        # Check for duplicate alias if one is being added
        if alias:
            try:
                existing_key, _ = host_manager.get_host_by_alias(alias)
                if existing_key:
                    return {
                        'status': 'error',
                        'error': f"Alias '{alias}' is already in use by host '{existing_key}'"
                    }
            except SshError as e:
                # Duplicate alias error from get_host_by_alias
                return {
                    'status': 'error',
                    'error': str(e)
                }

        # Use the regular password for sudo if sudo_password is not provided (and password exists)
        sudo_pass = sudo_password if sudo_password is not None else password
        host_manager.add_host(
            user=user,
            host=host,
            port=port,
            password=password,
            sudo_password=sudo_pass,
            alias=alias,
            description=description,
            keyfile=keyfile,
            key_passphrase=key_passphrase
        )

        result = {
            'status': 'success',
            'message': f"Host configuration for '{key}' added.",
            'key': key,
            'host': host,
            'user': user,
            'port': port,
            'auth_method': 'key' if keyfile else 'password'
        }
        if alias:
            result['alias'] = alias
        if description:
            result['description'] = description
        if keyfile:
            result['keyfile'] = keyfile
        return result
    except Exception as e:
        logger.error(f"Failed to add host configuration for {user}@{host}: {e}")
        raise


@mcp.tool()
async def ssh_host_update(
    host_name: Annotated[str, Field(description="The 'user@hostname' key or alias of the host to update")],
    password: Annotated[Optional[str], Field(description="New password. Omit to leave unchanged; pass an empty string to clear it (e.g. when switching to key-only auth)", secret=True)] = None,
    port: Annotated[Optional[int], Field(description="New SSH port. Omit to leave unchanged", ge=1, le=65535)] = None,
    sudo_password: Annotated[Optional[str], Field(description="New sudo password. Omit to leave unchanged; pass an empty string to clear it", secret=True)] = None,
    alias: Annotated[Optional[str], Field(description="New alias. Omit to leave unchanged; pass an empty string to clear it")] = None,
    description: Annotated[Optional[str], Field(description="New description. Omit to leave unchanged; pass an empty string to clear it")] = None,
    keyfile: Annotated[Optional[str], Field(description="New SSH private key path. Omit to leave unchanged; pass an empty string to clear it (e.g. when switching to password-only auth)")] = None,
    key_passphrase: Annotated[Optional[str], Field(description="New passphrase for the SSH key. Omit to leave unchanged; pass an empty string to clear it", secret=True)] = None
) -> dict:
    """
    Update one or more fields of an existing host configuration - this is the safe way
    to rotate a password, change a port, or adjust other settings without ever needing
    to read or hand-edit the host configuration TOML file (which stores every host's
    credentials in plaintext - never read it directly; this tool, ssh_conn_add_host,
    ssh_host_remove, and ssh_host_list cover everything you should need).

    Only the fields you pass are changed - any parameter left at its default
    (omitted/`None`) keeps its current value. To clear a field entirely (e.g. drop a
    password when switching a host to key-only auth), pass an empty string `""` rather
    than omitting it. `user`/`host` themselves can't be changed this way (that changes
    the 'user@host' key identity) - remove and re-add instead if you need that.

    Prefer this over ssh_host_remove + ssh_conn_add_host for adjusting an existing
    host: remove+re-add loses every field you don't explicitly resupply, since
    ssh_conn_add_host has no knowledge of the entry it just deleted.

    `host_name` may be either the 'user@hostname' key or a configured alias - resolved
    the same way as ssh_conn_connect.

    Warn the user that any new password/passphrase value passed here will be visible
    to the LLM, the same caveat as ssh_conn_add_host.

    Returns:
        On success: `{'status': 'success', 'message', 'key', 'updated_fields' (list of
        field names that were actually changed)}`. On failure (the update would leave
        neither a password nor a keyfile set, or a duplicate alias):
        `{'status': 'error', 'error': <message>}` - not a raised exception.

    Raises:
        SshError: If `host_name` doesn't resolve to any configured host (tried as both
        key and alias).
    """
    resolved_key, existing = host_manager.resolve_host(host_name)

    updates = {
        'password': password,
        'port': port,
        'sudo_password': sudo_password,
        'alias': alias,
        'description': description,
        'keyfile': keyfile,
        'key_passphrase': key_passphrase
    }

    merged = dict(existing)
    updated_fields = []
    for field, value in updates.items():
        if value is None:
            continue
        merged[field] = value if value != '' else None
        updated_fields.append(field)

    if not updated_fields:
        return {
            'status': 'error',
            'error': 'No fields provided to update - pass at least one of password/port/sudo_password/alias/description/keyfile/key_passphrase'
        }

    if not merged.get('password') and not merged.get('keyfile'):
        return {
            'status': 'error',
            'error': 'This update would leave the host with neither a password nor a keyfile configured - at least one authentication method must remain'
        }

    if merged.get('alias'):
        try:
            existing_alias_key, _ = host_manager.get_host_by_alias(merged['alias'])
            if existing_alias_key and existing_alias_key != resolved_key:
                return {
                    'status': 'error',
                    'error': f"Alias '{merged['alias']}' is already in use by host '{existing_alias_key}'"
                }
        except SshError as e:
            return {
                'status': 'error',
                'error': str(e)
            }

    try:
        host_manager.add_host(
            user=merged['parsed_user'],
            host=merged['parsed_host'],
            port=merged['port'],
            password=merged.get('password'),
            sudo_password=merged.get('sudo_password'),
            alias=merged.get('alias'),
            description=merged.get('description'),
            keyfile=merged.get('keyfile'),
            key_passphrase=merged.get('key_passphrase')
        )
    except Exception as e:
        logger.error(f"Failed to update host configuration for {resolved_key}: {e}")
        raise

    return {
        'status': 'success',
        'message': f"Host configuration for '{resolved_key}' updated.",
        'key': resolved_key,
        'updated_fields': updated_fields
    }


@mcp.tool()
@operation_tool
async def ssh_conn_status() -> dict:
    """
    Get essential SSH connection status information.
    
    Returns:
        Dictionary containing basic connection status (user, working directory, OS type)
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        status = mcp.ssh_client.get_connection_status(parent_tool='ssh_conn_status')

        # Get current working directory (use OS-appropriate command)
        if mcp.ssh_client.os_type == 'windows':
            cwd_result = mcp.ssh_client.run("cd", origin='connection_probe', parent_tool='ssh_conn_status')
        else:
            cwd_result = mcp.ssh_client.run("pwd", origin='connection_probe', parent_tool='ssh_conn_status')
        cwd = cwd_result.get_full_output().strip() if cwd_result.exit_code == 0 else "Unknown"

        return {
            'user': status.get('user', 'Unknown'),
            'host': status.get('host', 'Unknown'),
            'os_type': status.get('os_type', 'Unknown'),
            'current_directory': cwd,
            'connected': True
        }
    except Exception as e:
        logger.error(f"Failed to get status: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_conn_host_info() -> dict:
    """
    Get detailed SSH connection status and system information.

    Returns:
        Dictionary containing full connection status and detailed system info
        including hardware, memory, disk usage, and more. For a 'linux' or
        'flex' (non-Linux/macOS/Windows) connection, also includes
        'capabilities' (probed GNU/BusyBox-coreutils feature support) and
        'capability_warnings' (plain-English list of confirmed gaps) - see
        ssh_conn_connect's docstring for what these mean.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        status = mcp.ssh_client.get_connection_status(parent_tool='ssh_conn_host_info')
        system_info = mcp.ssh_client.full_status(parent_tool='ssh_conn_host_info')
        result = {
            'connection': status,
            'system': system_info
        }
        if mcp.ssh_client.capabilities:
            result['capabilities'] = mcp.ssh_client.capabilities
            warnings = describe_capabilities(mcp.ssh_client.capabilities)
            if warnings:
                result['capability_warnings'] = warnings
        return result
    except Exception as e:
        logger.error(f"Failed to get host info: {e}")
        raise


@mcp.tool()
async def ssh_host_use_config(
    config_path: Annotated[Optional[str], Field(description="Path to an alternate host configuration TOML file to switch to. Omit or pass an empty string to revert to the server's default configuration file")] = None
) -> dict:
    """
    Switch which host configuration file ssh_host_list, ssh_conn_connect,
    ssh_conn_add_host, ssh_host_update, and ssh_host_remove all operate against, for
    the rest of this session (or until you call this again) - not just for one call.
    This is the same "one active thing at a time" model ssh_conn_connect uses for SSH
    connections, applied to host configuration files instead; the two are completely
    independent (switching config files doesn't affect any current SSH connection,
    and vice versa).

    Use this when you want to browse or use hosts from a different TOML file than
    the server's default - e.g. a separate list of hosts for a different
    environment/project. The alternate file must already exist and be a valid host
    configuration TOML file (see ssh_conn_add_host's docstring for the format) - this
    deliberately does NOT auto-create a missing file the way the server's own default
    config file is created on first run, since an LLM-supplied path with a typo
    should fail loudly rather than silently create a stray file somewhere.

    Omit `config_path` (or pass `""`) to switch back to the server's original default
    configuration file.

    ssh_host_list's response always includes `config_path` showing whichever file is
    currently active, so you can check before mutating anything with
    ssh_conn_add_host/ssh_host_update/ssh_host_remove.

    Returns:
        On success: `{'status': 'success', 'message', 'config_path', 'is_default'
        (bool), 'host_count'}`. On failure (path doesn't exist, path is a directory,
        or the file fails to parse as valid host config TOML):
        `{'status': 'error', 'error': <message>}` - not a raised exception.
    """
    global host_manager

    if not config_path:
        host_manager = _default_host_manager
    else:
        resolved_path = Path(config_path).expanduser()
        if not resolved_path.exists():
            return {
                'status': 'error',
                'error': f"Path '{resolved_path}' does not exist - this tool will not "
                         f"auto-create an alternate config file the way the server's "
                         f"own default one is created on first run. Create the file "
                         f"first (or point at an existing one) and try again."
            }
        if resolved_path.is_dir():
            return {
                'status': 'error',
                'error': f"Path '{resolved_path}' is a directory, not a file"
            }

        candidate = SshHostManager(config_path=resolved_path)
        try:
            host_count = len(candidate.hosts)  # Force a parse now, so failures surface here
        except Exception as e:
            return {
                'status': 'error',
                'error': f"Failed to load '{resolved_path}' as a host configuration file: {e}"
            }
        host_manager = candidate

    return {
        'status': 'success',
        'message': f"Now using '{host_manager.config_path}' for host configuration",
        'config_path': str(host_manager.config_path),
        'is_default': host_manager is _default_host_manager,
        'host_count': len(host_manager.hosts)
    }


@mcp.tool()
async def ssh_host_list() -> dict:
    """
    List all configured SSH hosts with their aliases and descriptions. This is the
    ONLY correct way to see what hosts are configured - never read the host
    configuration TOML file directly (ssh_conn_add_host's docstring names its path).
    That file stores every host's password, sudo password, and key passphrase in
    plaintext; this tool deliberately omits all of that and returns only the fields
    below. If you need to add, change, or remove a host, use ssh_conn_add_host,
    ssh_host_update, or ssh_host_remove instead of editing the file - between those
    and ssh_host_use_config, there is no legitimate reason to open it.

    Lists hosts from whichever config file is currently active - the server's
    default unless ssh_host_use_config was called to switch to an alternate file.
    The returned `config_path` always shows which one that is.

    Returns:
        Dictionary with:
        - hosts: List of host information dictionaries, each containing:
          - key: The 'user@host' key
          - alias: Optional short name for the host
          - description: Optional description of the host
        - config_path: The host configuration file this list came from
    """
    hosts_info = []
    for key, details in host_manager.hosts.items():
        host_entry = {"key": key}
        if details.get('alias'):
            host_entry['alias'] = details['alias']
        if details.get('description'):
            host_entry['description'] = details['description']
        hosts_info.append(host_entry)
    return {
        "hosts": hosts_info,
        "config_path": str(host_manager.config_path)
    }

@mcp.tool()
async def ssh_host_remove(
    host_name: Annotated[str, Field(description="The 'user@hostname' identifier of the host to remove")]
) -> dict:
    """
    Remove a host configuration from the host configuration TOML file (see
    ssh_conn_add_host's docstring for its exact path and why you should never read it
    directly - use ssh_host_list to see what's configured instead).

    To change a host's password/port/etc. rather than deleting it, use
    ssh_host_update instead - removing and re-adding loses every field you don't
    explicitly resupply.

    Returns:
        Dictionary with operation status
    """
    try:
        if host_manager.remove_host(host_name):
            return {
                'status': 'success',
                'message': f"Host configuration for '{host_name}' removed",
                'remaining_hosts': list(host_manager.hosts.keys())
            }
        else:
            return {
                'status': 'error',
                'error': f"Host '{host_name}' not found in configuration",
                'hosts': list(host_manager.hosts.keys())
            }
    except Exception as e:
        logger.error(f"Failed to remove host configuration for {host_name}: {e}")
        raise

@mcp.tool()
@operation_tool
async def ssh_host_disconnect() -> dict:
    """
    Disconnect the current SSH connection if one exists.
    
    Use this when you want to explicitly close the current SSH connection
    before connecting to a different host or when you're done with SSH operations.
    
    Returns:
        Dictionary with disconnection status
    """
    try:
        if mcp.ssh_client is None:
            logger.info("No active SSH connection to disconnect")
            return {
                'status': 'success',
                'message': "No active SSH connection to disconnect",
                'was_connected': False
            }
            
        logger.info("Disconnecting active SSH connection")
        host = mcp.ssh_client.get_connection_status(parent_tool='ssh_host_disconnect').get('host', 'unknown')
        user = mcp.ssh_client.get_connection_status(parent_tool='ssh_host_disconnect').get('user', 'unknown')
        
        mcp.ssh_client.close()
        mcp.ssh_client = None
        
        return {
            'status': 'success',
            'message': f"Successfully disconnected from {user}@{host}",
            'was_connected': True,
            'disconnected_from': f"{user}@{host}"
        }
    except Exception as e:
        logger.error(f"Failed to disconnect SSH connection: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'was_connected': mcp.ssh_client is not None
        }

@mcp.tool()
@operation_tool
async def ssh_conn_verify_sudo() -> dict:
    """
    Check whether elevated access is available on the remote system, without running
    any privileged command. Call this before using `use_sudo=True` on other tools if
    you're not sure elevation will succeed.

    On Linux/macOS: probes whether `sudo` is available and whether it needs a
    password (via a passwordless `sudo -n` check, then a password-based check if
    that fails). Tools called afterward with `use_sudo=True` will use whichever mode
    this detected.

    On Windows: there is no per-command elevation - checks whether the current SSH
    session itself is already running as Administrator. If it's not, no tool call
    can become elevated; you must reconnect as an Administrator account instead.

    Returns:
        Dictionary with:
        - available (bool): True if sudo/elevation can be used at all (passwordless
          OR password-based on Linux/macOS; same as `passwordless` on Windows, since
          there's no separate password-based mode there)
        - passwordless (bool): True if no password is needed (passwordless sudo, or
          an already-elevated Windows session)
        - requires_password (bool): True if sudo works but needs a password (always
          False on Windows)
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        # Windows: Check elevation status
        if mcp.ssh_client.os_type == 'windows':
            is_elevated = getattr(mcp.ssh_client, '_is_elevated', False)
            return {
                "available": is_elevated,
                "passwordless": is_elevated,  # Elevated sessions don't need password
                "requires_password": False
            }

        # Linux/macOS: Check sudo access
        # First check for passwordless sudo
        passwordless = False
        try:
            # Use -n flag to prevent sudo from asking for a password
            result = mcp.ssh_client.run("sudo -n true", io_timeout=5.0,
                                         origin='sudo_probe', parent_tool='ssh_conn_verify_sudo')
            if result.exit_code == 0:
                passwordless = True
        except Exception as e:
            logger.debug(f"Passwordless sudo check failed: {e}")
            passwordless = False

        # Check if sudo with password works
        requires_password = False
        if not passwordless:
            # First check if we have a sudo password configured
            if mcp.ssh_client.sudo_password:
                try:
                    # This will use the sudo password via the _handle_sudo method
                    result = mcp.ssh_client.run("true", sudo=True, io_timeout=5.0,
                                                 origin='sudo_probe', parent_tool='ssh_conn_verify_sudo')
                    if result.exit_code == 0:
                        requires_password = True
                except Exception as e:
                    logger.debug(f"Password sudo check failed: {e}")
                    requires_password = False
            else:
                # Even without a configured sudo password, check if sudo is available
                # This will detect if the user has sudo access but we just don't have the password
                try:
                    # Run a command that checks if the user is in sudoers file
                    # This won't actually execute sudo but just checks if the user is in sudoers
                    result = mcp.ssh_client.run("sudo -l -U $(whoami) | grep -q '(ALL'", io_timeout=5.0,
                                                 origin='sudo_probe', parent_tool='ssh_conn_verify_sudo')
                    requires_password = result.exit_code == 0
                except Exception as e:
                    logger.debug(f"Sudo access check failed: {e}")

                    # Try another approach - check if user is in sudo group
                    try:
                        result = mcp.ssh_client.run("groups | grep -q '\\bsudo\\b'", io_timeout=5.0,
                                                     origin='sudo_probe', parent_tool='ssh_conn_verify_sudo')
                        requires_password = result.exit_code == 0
                    except Exception as e2:
                        logger.debug(f"Sudo group check failed: {e2}")
                        requires_password = False
                
        return {
            "available": passwordless or requires_password,
            "passwordless": passwordless,
            "requires_password": requires_password
        }
    except Exception as e:
        logger.error(f"Failed to verify sudo access: {e}")
        raise


# ===================
# Task Operation Tools
# ===================


@mcp.tool()
@threaded_tool
async def ssh_task_status(
    pid: Annotated[int, Field(description="Process ID to check status for")]
) -> dict:
    """
    Check the liveness of a background task by PID (from ssh_task_launch, or any other
    real remote PID - e.g. the `pid` field from ssh_cmd_run). This does a live check on
    the remote host every call, not a cached lookup, so it's safe to call repeatedly.

    Returns:
        `{'pid', 'status', 'running' (bool, True iff status=='running'), 'timestamp'}`.
        `status` is one of:
        - 'running': the process currently exists.
        - 'exited': the process is gone - it either completed or was killed; there's
          no way to distinguish which, or recover its exit code, from this tool alone.
        - 'invalid': the given `pid` isn't a valid positive integer.
        - 'error': the liveness check itself failed (e.g. connection issue) - this
          does NOT mean the process exited, just that its status is unknown.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        status = mcp.ssh_client.task_status(pid)
        result = {
            'pid': pid,
            'status': status,
            'running': status == 'running',
            'timestamp': datetime.now(UTC).isoformat()
        }
        if status == 'error':
            reason = getattr(mcp.ssh_client.task_ops, 'last_status_error', None)
            result['reason'] = reason or "the status check failed"
            result['next_step'] = ("This does NOT mean the task exited - its state is unknown. "
                                   "Call ssh_task_status again shortly.")
        return result
    except Exception as e:
        logger.error(f"Failed to get task status: {e}")
        raise


@mcp.tool()
@threaded_tool
async def ssh_task_kill(
    pid: Annotated[int, Field(description="Process ID to terminate")],
    signal: Annotated[int, Field(description="Signal to send (15=TERM, 9=KILL)", ge=1, le=15)] = 15,
    use_sudo: Annotated[bool, Field(description="Use sudo for the kill operation")] = False,
    force: Annotated[bool, Field(description="Force kill with SIGKILL if process doesn't exit")] = True,
    wait_seconds: Annotated[float, Field(description="Seconds to wait before force kill", gt=0)] = 1.0
) -> dict:
    """
    Terminate a background task (launched via ssh_task_launch, or any other real
    remote PID) by sending a signal to its PID.

    If force=True and the process doesn't exit after wait_seconds,
    it will be forcibly killed with SIGKILL (signal 9).

    With `use_sudo=True` on a 'linux'/'flex' connection with a BusyBox-style
    `ps` that doesn't support `-o pgid=` (check `ssh_conn_connect`/
    `ssh_conn_host_info`'s `capabilities`), this raises a clear error instead
    of running - there is no clean fallback, since killing just the captured
    PID (rather than its whole process group) can leave the sudo'd command's
    real child process(es) running as orphans, exactly the failure mode this
    check exists to prevent. Not an issue with `use_sudo=False`.

    Returns:
        `{'pid', 'result', 'signal', 'force_kill_used', 'timestamp'}`. `result` is
        one of:
        - 'killed': confirmed terminated (by the initial signal or the force-kill
          fallback - see `force_kill_used` to tell which). Terminal - nothing left
          to check.
        - 'already_exited': the process was already gone before any signal was sent.
          Terminal.
        - 'failed_to_kill': still running after both the signal and force-kill
          attempt (or after the signal alone, if `force=False`). Not terminal - the
          process is still alive; consider retrying or investigating why it won't die.
        - 'invalid_pid': `pid` wasn't a valid positive integer - no signal was sent.
        - 'error': the kill attempt itself failed unexpectedly (e.g. connection
          issue) - the process's actual state is unknown, not necessarily still running.

        `force_kill_used` is True iff the SIGKILL fallback was actually attempted
        (the initial signal alone was not enough to end the process), regardless of
        whether the fallback itself succeeded - False if the initial signal alone
        was sufficient, the process was already gone, `pid` was invalid, or
        `force=False` so no fallback was ever attempted.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        force_kill_signal = 9 if force else None
        result, force_kill_used = mcp.ssh_client.task_kill(pid, signal, use_sudo, force_kill_signal, wait_seconds)
        return {
            'pid': pid,
            'result': result,
            'signal': signal,
            'force_kill_used': force_kill_used,
            'timestamp': datetime.now(UTC).isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to kill task: {e}")
        raise


# ===================
# Cmd Operation Tools
# ===================


def _inline_tail(lines, limit):
    """The most recent whole lines that fit in `limit` characters -> (text, line_count).
    A single line bigger than the limit is cut to its last `limit` characters."""
    picked, size = [], 0
    for line in reversed(lines):
        if size + len(line) > limit:
            if not picked:
                picked.append(line[-limit:])
            break
        picked.append(line)
        size += len(line)
    picked.reverse()
    return ''.join(picked), len(picked)


def _output_fields(handle, stdout_key='output', stderr_key='stderr'):
    """stdout/stderr for a command response, bounded to OutputLimits.inline each.

    Always adds `output_truncated` / `stderr_truncated`. When either is true, adds the
    line counts and an `output_note` telling the caller how to page the rest with
    ssh_cmd_output(start_line=...), or that the earliest lines were dropped for good
    (past the per-command size limit) - so a response never silently looks complete.
    """
    fields, notes = {}, []
    for stream, key, prefix in (('stdout', stdout_key, 'output'), ('stderr', stderr_key, 'stderr')):
        if stream == 'stdout':
            total, dropped = handle.total_lines, handle.dropped_lines
        else:
            total, dropped = handle.total_stderr_lines, handle.dropped_stderr_lines
        text, returned = _inline_tail(handle.retained_lines(stream), OutputLimits.inline)
        fields[key] = text
        truncated = returned < total
        fields[f'{prefix}_truncated'] = truncated
        if not truncated:
            continue
        fields[f'{prefix}_lines_total'] = total
        fields[f'{prefix}_lines_returned'] = returned
        fields[f'{prefix}_lines_dropped'] = dropped
        first_returned = total - returned + 1
        note = f"{stream}: this response shows only lines {first_returned}-{total} of {total}."
        if dropped + 1 < first_returned:
            note += (f" Lines {dropped + 1}-{first_returned - 1} can be read with "
                     f"ssh_cmd_output(handle_id={handle.id}, stream='{stream}', start_line=N, lines=M).")
        if dropped:
            note += (f" Lines 1-{dropped} exceeded the server's per-command output limit and were "
                     f"dropped (not recoverable) - for very large output, redirect it to a file or "
                     f"use ssh_task_launch.")
        notes.append(note)
    if notes:
        fields['output_note'] = ' '.join(notes)
    return fields


@mcp.tool()
async def ssh_cmd_run(
        command: Annotated[str, Field(description="Command to execute on remote host")],
        io_timeout: Annotated[float, Field(description="Max seconds of SILENCE (no output) before giving up on waiting. Does NOT kill the remote command - hands off to background monitoring and returns control to you. Note a single call never blocks longer than the server's wait cap (default 50s, since most MCP clients abort tool calls at ~60s) - raising this above that only matters for how long silence is tolerated within the cap. For package installs, Docker pulls, large downloads or compilation, prefer ssh_task_launch. If hit, call ssh_cmd_check_status or ssh_cmd_output to check back rather than rerunning.")] = 60.0,
        runtime_timeout: Annotated[Optional[float], Field(description="Total wall-clock cap in seconds, regardless of output activity. Unlike io_timeout/wait_timeout, hitting this DOES attempt to kill the remote command - it's the only hard ceiling. Set generously for long operations (installs/downloads can need 10-60+ minutes) - this should be a safety net, not a UX mechanism.", gt=0)] = None,
        use_sudo: Annotated[bool, Field(description="Run command with sudo")] = False,
        cwd: Annotated[Optional[str], Field(description="Run the command in this directory, for this call only (Linux/macOS only - not yet supported on Windows). Nothing is remembered between calls: each ssh_cmd_run is an independent process, so pass cwd again on every call where it matters, or chain 'cd dir && command' yourself. Fails closed - if the directory doesn't exist, the command is never executed at all (status='cwd_not_found'), so there's no ambiguity about where anything ran.")] = None,
        wait_timeout: Annotated[Optional[float], Field(description="Max seconds to wait in THIS call, regardless of output activity - unlike io_timeout, fires even if the command is actively producing output. Does NOT kill the remote command, same non-destructive handoff as io_timeout. Use this when you want to check in periodically on a command that's chatty but long-running (e.g. a verbose Docker pull), rather than being blocked until it finishes or goes quiet. Values above the server's wait cap (default 50s) are clamped to it - the response then has wait_capped=true.", gt=0)] = None
) -> dict:
    """
    Execute a command on the remote host and BLOCK until it completes, an io_timeout (silence),
    a wait_timeout (elapsed cap), or a runtime_timeout (hard cap) occurs.

    Timeout semantics (read this before choosing values):
    - io_timeout and wait_timeout firing do NOT mean the remote command stopped - it genuinely
      keeps running. Monitoring is handed off to a background thread so output/exit code continue
      to be collected; the response has status='io_timeout'/'wait_timeout', still_running=true,
      and an id/pid you can use with ssh_cmd_check_status(handle_id=...) to poll again later, or
      ssh_cmd_output(handle_id=...) to read output collected so far (including output produced
      after this call returned). Do not rerun the command. You can also still decide to end it
      early with ssh_cmd_kill(handle_id=...) at any point after either of these fires.
    - io_timeout fires only on SILENCE (no output for N seconds) - a command that keeps producing
      output never triggers it, however long it runs.
    - wait_timeout fires after N seconds of TOTAL elapsed wait, regardless of activity - use this
      if you want to check in periodically on a long-running command even while it's actively
      producing output, rather than being blocked until completion.
    - runtime_timeout is the only knob that DOES attempt to terminate the remote command - a hard
      safety ceiling, not a UX mechanism. Set it generously (much longer than the command should
      ever realistically take).
    - A single call never blocks longer than the server's wait cap (default 50s, configurable
      via --max-wait / MCP_SSH_MAX_WAIT), because most MCP clients abort a tool call at ~60s and
      the id/pid handoff would be lost. Longer waits are clamped: the response is a normal
      status='wait_timeout' handoff with wait_capped=true. For anything that may take more than
      about a minute, ssh_task_launch is usually the better tool.
    - For commands that should survive you disconnecting/reconnecting entirely (not just this
      call returning), use ssh_task_launch instead - it runs fully detached from this SSH session,
      whereas a command started here (even after surviving io_timeout/wait_timeout) is still tied
      to the current connection's lifetime.

    You can access command history using 'ssh_cmd_history' to see previous commands and their output.
    Commands run this way always appear there with origin='user' - pass include_internal=False to
    ssh_cmd_history to hide unrelated internal plumbing from other tools (e.g. ssh_file_write's sudo dance).

    Windows targets: commands run under cmd.exe (CMD syntax), not PowerShell. For PowerShell,
    run it explicitly: powershell -NoProfile -Command "...".

    Working directory: each call is an independent remote process (like a GitHub Actions step
    or Ansible task, not a continuous shell) - nothing is remembered between calls, including
    'cd'. Running ssh_cmd_run("cd /var/log") does NOT affect a later ssh_cmd_run("ls"); it will
    still list the login directory. Use absolute paths, chain "cd dir && command" within one
    call, or pass the cwd parameter to run this specific call in a specific directory
    (Linux/macOS only for now).

    Returns:
        Dictionary containing command output, status, and metadata. The handle
        identifier is returned here as `'id'`, but every other tool that accepts it
        (ssh_cmd_check_status, ssh_cmd_kill, ssh_cmd_output) names the same parameter
        `handle_id` - pass this value there. `output` (stdout) and `stderr` are always
        two SEPARATE fields, never interleaved into one combined stream - a command
        that succeeds can still have written to stderr (warnings, progress meters,
        non-fatal messages), so check `stderr` even on `status='success'`.

        Each stream is returned inline up to its most recent ~32 KB. `output_truncated`
        and `stderr_truncated` are always present; when true, the response also has
        line counts and an `output_note` saying how to read the rest with
        ssh_cmd_output(handle_id=..., start_line=...), or that the earliest lines were
        dropped (the server keeps ~2 MB per stream per command). For very large output,
        redirect it to a file and read that instead.

        `status` is one of:
        - 'success': command completed with exit code 0. `exit_code`, `output`, `stderr` populated.
        - 'command_failed': completed with a non-zero exit code. `exit_code`, `output`, `stderr` populated.
        - 'cwd_not_found': the `cwd` parameter didn't exist on the remote host - the
          command was NOT executed at all (fails closed).
        - 'io_timeout': no output within `io_timeout` seconds - remote command was NOT
          killed, still_running=true. Poll with ssh_cmd_check_status(handle_id=...).
        - 'wait_timeout': `wait_timeout` elapsed regardless of activity - remote command was
          NOT killed, still_running=true. Poll with ssh_cmd_check_status(handle_id=...).
        - 'runtime_timeout': `runtime_timeout` exceeded - an attempt was made to kill
          the remote command (see ssh_cmd_check_status's `'killed'` status to confirm).
        - 'sudo_required': `use_sudo=True` but elevation isn't available (see
          ssh_conn_verify_sudo before retrying).
        - 'busy': another ssh_cmd_run is still WAITING in the foreground on this
          connection - only one call can wait at a time. A command that was already
          handed off (io_timeout/wait_timeout) keeps running in the background and does
          NOT block new ssh_cmd_run calls; several remote commands can be running at once.
        - 'error': unexpected failure (e.g. connection dropped).
    """
    if not mcp.ssh_client:
        return {
            'status': 'error',
            'error': _not_connected_message(),
            'command': command,
            'timestamp': datetime.now(UTC).isoformat()
        }

    # Never block longer than the foreground cap - past ~60s most MCP clients abort the
    # call and the id/pid handoff is lost. Capping wait_timeout keeps the normal handoff.
    effective_wait = wait_timeout
    wait_capped = False
    if max_foreground_wait and (wait_timeout is None or wait_timeout > max_foreground_wait):
        effective_wait = max_foreground_wait
        wait_capped = True

    try:
        # Run in a worker thread: client.run() blocks, and blocking the event loop would
        # stall every other tool call (even ssh_cmd_history/ssh_cmd_check_status) until
        # this command finished.
        # One foreground operation at a time (see operation_tool). Held only while this
        # call waits - a command handed off at io/wait_timeout keeps running without it.
        global _foreground_op
        if not _foreground_lock.acquire(blocking=False):
            return {
                'status': 'busy',
                'command': command,
                'error': _busy_message(),
                'timestamp': datetime.now(UTC).isoformat()
            }
        # Owner record for other callers' busy message (id None = a foreground command)
        fg = _Operation(None, 'ssh_cmd_run', command if len(command) <= 120 else command[:117] + '...')
        _foreground_op = fg
        try:
            handle = await asyncio.to_thread(
                mcp.ssh_client.run, command, io_timeout, runtime_timeout, use_sudo,
                cwd=cwd, wait_timeout=effective_wait
            )
        finally:
            if _foreground_op is fg:
                _foreground_op = None
            _foreground_lock.release()
        return {
            'status': 'success',
            'id': handle.id,
            'command': command,
            'exit_code': handle.exit_code,
            **_output_fields(handle),
            'pid': handle.pid,
            'cwd': handle.cwd,
            'start_time': handle.start_ts.isoformat(),
            'end_time': handle.end_ts.isoformat() if handle.end_ts else None
        }
    except CwdNotFound as e:
        logger.warning(f"cwd does not exist, command was not executed: {e.cwd}")
        return {
            'status': 'cwd_not_found',
            'command': command,
            'cwd': e.cwd,
            'error': str(e),
            'note': "The command was NOT executed - this fails closed, so nothing ran anywhere unexpected.",
            'timestamp': datetime.now(UTC).isoformat()
        }
    except CommandTimeout as e:
        logger.warning(f"Command {e.reason} after {e.seconds}s: {command}")
        handle = e.handle
        trigger_desc = (
            f"no output for {e.seconds}s" if e.reason == 'io_timeout'
            else f"{e.seconds}s of total elapsed wait, regardless of activity"
        )

        result = {
            'status': e.reason,  # 'io_timeout' or 'wait_timeout'
            'id': handle.id if handle else None,
            'pid': handle.pid if handle else None,
            'command': command,
            'timeout_seconds': e.seconds,
            **(_output_fields(handle) if handle else {'output': None}),
            'still_running': True,
            'next_step': (
                f"The remote command was NOT killed - only local monitoring handed off to background "
                f"monitoring after {trigger_desc}. It is still running on the remote host and output/exit "
                f"code continue to be collected. Call ssh_cmd_check_status(handle_id={handle.id}) to poll, "
                f"ssh_cmd_output(handle_id={handle.id}) for output collected so far (including output "
                f"produced after this call returned), or ssh_cmd_kill(handle_id={handle.id}) if you want "
                f"to end it early. Do not rerun this command."
            ) if handle else (
                "No command handle is available to check back with. Inspect the remote process "
                "directly if you need to confirm its status."
            ),
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }
        if wait_capped and e.reason == 'wait_timeout':
            result['wait_capped'] = True
            result['requested_wait_timeout'] = wait_timeout
            result['note'] = (
                f"This call returned after {effective_wait:g}s, the server's per-call wait cap "
                f"(most MCP clients abort tool calls at ~60s, which would lose this handoff). "
                f"This is expected for long commands - just poll as described in next_step."
            )
        return result
    except CommandRuntimeTimeout as e:
        logger.warning(f"Command runtime timeout after {e.seconds}s: {command}")
        return {
            'status': 'runtime_timeout',
            'id': e.handle.id,
            'command': command,
            'timeout_seconds': e.seconds,
            'pid': e.handle.pid,
            **(_output_fields(e.handle) if hasattr(e.handle, 'retained_lines') else {'output': None}),
            'start_time': e.handle.start_ts.isoformat() if hasattr(e.handle, 'start_ts') else None,
            'end_time': e.handle.end_ts.isoformat() if hasattr(e.handle, 'end_ts') else None,
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }
    except CommandFailed as e:
        logger.warning(f"Command failed with exit code {e.exit_code}: {command}")
        failed_handle = getattr(e, 'handle', None)
        return {
            'status': 'command_failed',
            **({'id': failed_handle.id} if failed_handle else {}),
            'command': command,
            'exit_code': e.exit_code,
            **(_output_fields(failed_handle, stdout_key='stdout') if failed_handle
               else {'stdout': e.stdout, 'stderr': e.stderr}),
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }
    except SudoRequired as e:
        logger.warning(f"Sudo required but not available: {command}")
        return {
            'status': 'sudo_required',
            'command': command,
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }
    except BusyError as e:
        logger.warning(f"Command execution blocked - another command is running: {command}")
        return {
            'status': 'busy',
            'command': command,
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }
    except Exception as e:
        logger.error(f"Command execution failed: {e}")
        lost = await asyncio.to_thread(_handle_possible_connection_loss, e)
        if lost is not None:
            return {
                'status': 'error',
                'error_type': 'connection_lost',
                'command': command,
                'error': str(lost),
                'timestamp': datetime.now(UTC).isoformat()
            }
        return {
            'status': 'error',
            'command': command,
            'error': str(e),
            'error_type': type(e).__name__,
            'timestamp': datetime.now(UTC).isoformat()
        }


@mcp.tool()
@threaded_tool
async def ssh_cmd_kill(
    handle_id: Annotated[int, Field(description="Command handle ID to kill - the 'id' field from ssh_cmd_run's response")],
    signal: Annotated[int, Field(description="Signal to send (15=TERM, 9=KILL)", ge=1, le=15)] = 15,
    force: Annotated[bool, Field(description="Force kill with SIGKILL if process doesn't exit")] = True,
    wait_seconds: Annotated[float, Field(description="Seconds to wait before force kill", gt=0)] = 1.0
) -> dict:
    """
    Terminate a currently running command by its handle ID (the `id` field from
    ssh_cmd_run's response).

    This tool is specifically for killing commands started with ssh_cmd_run - it
    looks up `handle_id` in this connection's command history, not by raw PID. For
    background tasks launched with ssh_task_launch, use ssh_task_kill with the PID
    instead. Raises an error if `handle_id` doesn't exist in history (e.g. from a
    previous connection - handles don't survive reconnects) or has no associated PID.

    If force=True and the process doesn't exit after wait_seconds,
    it will be forcibly killed with SIGKILL (signal 9).

    Returns:
        `{'handle_id', 'pid', 'result', 'signal', 'force_kill_used', 'timestamp'}`.
        `result` is one of:
        - 'not_running': the command was already confirmed not running before any
          signal was sent (checked first, via a live PID status check) - nothing to do.
        - 'killed': confirmed terminated (by the signal or the force-kill fallback -
          see `force_kill_used` to tell which).
        - 'already_exited': the process was gone by the time the signal landed.
        - 'failed_to_kill': still running after the signal (and force-kill, if
          `force=True`) - not terminal, the process is still alive.
        - 'invalid_pid': the command's tracked PID wasn't a valid positive integer.
        - 'error': the kill attempt itself failed unexpectedly - the process's real
          state is unknown.

        `force_kill_used` is True iff the SIGKILL fallback was actually attempted
        (the initial signal alone was not enough), regardless of whether the
        fallback itself succeeded - False if the initial signal alone was
        sufficient, the process was already gone, or `force=False`.

        Note: after a successful kill (`'killed'` or `'already_exited'`, or the early
        `'not_running'` case), a later `ssh_cmd_check_status` call for this same
        `handle_id` will report status `'killed'`.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        # Get the command handle from history
        history = mcp.ssh_client.history()
        handle_info = next((h for h in history if h.get('id') == handle_id), None)
        
        if not handle_info:
            raise SshError(f"No command found with handle ID: {handle_id}")
            
        pid = handle_info.get('pid')
        if not pid:
            raise SshError(f"Command handle {handle_id} has no associated PID")
            
        # Check if the command is still running
        status = mcp.ssh_client.task_status(pid)
        if status != 'running':
            # Confirmed not running (e.g. runtime_timeout already killed it) - record
            # this so ssh_cmd_check_status stops reporting 'unknown_still_running'.
            mcp.ssh_client.mark_kill_confirmed(handle_id)
            return {
                'handle_id': handle_id,
                'pid': pid,
                'result': 'not_running',
                'message': f"Command is not running (status: {status})",
                'timestamp': datetime.now(UTC).isoformat()
            }

        # Kill the process using the existing task_kill method
        force_kill_signal = 9 if force else None
        result, force_kill_used = mcp.ssh_client.task_kill(pid, signal, False, force_kill_signal, wait_seconds)
        if result in ('killed', 'already_exited'):
            mcp.ssh_client.mark_kill_confirmed(handle_id)

        return {
            'handle_id': handle_id,
            'pid': pid,
            'result': result,
            'signal': signal,
            'force_kill_used': force_kill_used,
            'timestamp': datetime.now(UTC).isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to kill command: {e}")
        raise


@mcp.tool()
async def ssh_cmd_check_status(
    handle_id: Annotated[int, Field(description="Command handle ID to check status for - the 'id' returned by ssh_cmd_run, including in its io_timeout/wait_timeout response")],
    wait_seconds: Annotated[float, Field(description="Seconds to wait before checking. Clamped to the server's per-call wait cap (default 50s, since most MCP clients abort tool calls at ~60s) - the response's waited_seconds shows the wait actually applied. Short waits (1-10s) with repeated polling work best.", gt=0)] = 5.0
) -> dict:
    """
    Wait for the specified duration, then check the status of a command started with
    ssh_cmd_run. Call this repeatedly - it's designed to be polled - after
    ssh_cmd_run returns status='io_timeout' or 'wait_timeout' (the remote command is
    still genuinely running in both cases - background monitoring keeps collecting
    its output/exit code), until you get a TERMINAL status below. Do not rerun the
    original command while polling.

    Terminal status values (nothing left to wait for, stop polling):
    - 'completed': confirmed finished, exit_code is populated - including for commands
      that survived an io_timeout/wait_timeout, since background monitoring keeps
      watching for the real exit code.
    - 'killed': the remote process was confirmed terminated (e.g. runtime_timeout killed it,
      or a prior ssh_cmd_kill call found it already gone) - exit_code is not known.
    - 'completed_exit_code_unknown': rare fallback - monitoring stopped without a confirmed
      exit code (should only really happen from before background monitoring existed, or
      after an unexpected error) and a live check now confirms the remote process is no
      longer running. Only its output (via ssh_cmd_output) is available, not its exit code.

    Non-terminal status values (keep polling):
    - 'running': still being actively monitored, not yet finished.
    - 'unknown_still_running': rare fallback (same caveat as 'completed_exit_code_unknown')
      where a live check confirms the remote command is still actually running. Not a
      failure - call this tool again to keep checking.

    Other:
    - 'not_found': the handle_id doesn't exist (may be from a previous connection - handles don't
      survive reconnects, but background task PIDs from ssh_task_launch do).
    - 'unknown': the handle exists but its metadata is missing from history (rare, internal
      inconsistency) - treat like 'not_found'.

    Fallback behavior: if `handle_id` doesn't match any ssh_cmd_run handle, this tool
    also tries treating it as a raw background-task PID (same as ssh_task_status) and,
    if that succeeds, returns `{'pid', 'status': 'running'/'exited'/'invalid'/'error',
    'is_background_task': True, ...}` instead - a DIFFERENT status vocabulary than the
    one above. Prefer ssh_task_status directly for PIDs to avoid ambiguity.

    Returns:
        `{'handle_id', 'waited_seconds', 'status', 'exit_code', 'pid',
        'output_available', 'output_lines', 'timestamp'}`, plus `'next_step'` (guidance
        text) when `status` is non-terminal.

        For an OPERATION handle (the handle_id of an 'in_progress' response from a
        long-running tool such as ssh_archive_extract or ssh_file_transfer): returns
        `{'handle_id', 'status', 'tool', 'operation', 'started', 'ended', ...}` with status
        'running', 'completed' (plus 'result': the tool's full normal result) or 'failed'
        (plus 'error'). The wait ends early as soon as the operation finishes. While
        running it also returns 'elapsed_seconds' and 'progress': the current 'stage'
        (e.g. 'uploading archive', 'extracting on host'), 'bytes_done'/'bytes_total'/
        'percent' for transfers, counts such as 'files_searched', and 'last_update' /
        'seconds_since_update' - if those stop advancing for a long time, the operation
        may be stuck rather than just slow.
    """
    op = _operations.get(handle_id)
    if op is not None:
        wait = wait_seconds
        if max_foreground_wait and wait > max_foreground_wait:
            wait = max_foreground_wait
        await asyncio.to_thread(op.done.wait, wait)
        return _operation_status_response(op, wait)

    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        # Log the wait operation
        logger.info(f"Waiting {wait_seconds} seconds before checking status of handle {handle_id}")
        
        # Never outlast the client's request timeout (same cap as ssh_cmd_run) -
        # a 90s wait would come back as "Request timed out" in a 60s client
        if max_foreground_wait and wait_seconds > max_foreground_wait:
            wait_seconds = max_foreground_wait

        # Perform the actual wait
        await asyncio.sleep(wait_seconds)
        
        # After waiting, try to get the command handle
        try:
            # First try to get output which will tell us if the command is still running
            output = mcp.ssh_client.output(handle_id)
            
            # Get the handle info for metadata
            history = mcp.ssh_client.history()
            handle_info = next((h for h in history if h.get('id') == handle_id), None)
            
            if handle_info:
                # Command exists in history. Completion is only confirmed by a real
                # exit_code (set exclusively on genuine command completion) - end_ts
                # alone is NOT sufficient, since it's also set when monitoring stops
                # due to io_timeout, and the remote command is not killed in that case.
                exit_code = handle_info.get('exit_code')
                is_complete = exit_code is not None
                monitoring_ended = handle_info.get('end_ts') is not None
                kill_confirmed = handle_info.get('kill_confirmed', False)

                if kill_confirmed:
                    # runtime_timeout's own kill succeeded, or ssh_cmd_kill found it
                    # already gone - report 'killed' even if background monitoring
                    # also raced in an exit_code from the same kill (verified live on
                    # Windows: taskkill-ing a process still reports a numeric
                    # exit-status of 1 back over the channel, unlike Linux where a
                    # signal-killed process reports no exit-status at all - so
                    # is_complete could otherwise also be True here, and checking it
                    # first would misreport a confirmed, deliberate kill as an
                    # ordinary 'completed' with a meaningless exit code).
                    status = 'killed'
                elif is_complete:
                    status = 'completed'
                elif monitoring_ended:
                    # e.g. a prior io_timeout - we stopped watching, but that doesn't
                    # mean the remote command is still running. Live-check via
                    # task_status(pid) instead of assuming 'still running' forever -
                    # the same cross-platform PID-liveness check ssh_cmd_kill already
                    # uses. If it's confirmed gone, this is terminal (nothing left to
                    # wait for), even though the real exit code was never observed.
                    pid = handle_info.get('pid')
                    live_status = None
                    if pid:
                        try:
                            live_status = mcp.ssh_client.task_status(pid)
                        except Exception as task_status_err:
                            logger.debug(f"Live task_status check failed for pid {pid}: {task_status_err}")
                    if live_status == 'exited':
                        status = 'completed_exit_code_unknown'
                    else:
                        status = 'unknown_still_running'
                else:
                    status = 'running'

                result = {
                    'handle_id': handle_id,
                    'waited_seconds': wait_seconds,
                    'status': status,
                    'exit_code': exit_code,
                    'pid': handle_info.get('pid'),
                    'timestamp': datetime.now(UTC).isoformat(),
                    'output_available': True,
                    # Total stdout lines seen - `output` is only the last 50 (ssh_cmd_output's default)
                    'output_lines': handle_info.get('total_lines', len(output) if output else 0)
                }
                if status not in ('completed', 'killed', 'completed_exit_code_unknown'):
                    result['next_step'] = (
                        "Not confirmed complete. Call ssh_cmd_check_status again to keep polling, "
                        "or ssh_cmd_output(handle_id) to inspect output collected so far. Do not rerun this command."
                    )
                return result
            else:
                # Handle exists (since output didn't raise) but not in history
                return {
                    'handle_id': handle_id,
                    'waited_seconds': wait_seconds,
                    'status': 'unknown',
                    'timestamp': datetime.now(UTC).isoformat(),
                    'output_available': True,
                    'output_lines': len(output) if output else 0
                }
                
        except Exception as inner_e:
            # If we can't get the output, check if it's a background task by PID
            if isinstance(inner_e, SshError) and "No command handle" in str(inner_e):
                # Try to check if this is a PID instead
                try:
                    status = mcp.ssh_client.task_status(handle_id)
                    return {
                        'pid': handle_id,
                        'waited_seconds': wait_seconds,
                        'status': status,
                        'timestamp': datetime.now(UTC).isoformat(),
                        'is_background_task': True
                    }
                except Exception:
                    # Not a valid PID either
                    pass
            
            # If we get here, the handle/PID doesn't exist or another error occurred
            return {
                'handle_id': handle_id,
                'waited_seconds': wait_seconds,
                'status': 'not_found',
                'error': str(inner_e),
                'timestamp': datetime.now(UTC).isoformat()
            }
            
    except Exception as e:
        logger.error(f"Error in wait_and_check: {e}")
        return {
            'handle_id': handle_id,
            'waited_seconds': wait_seconds,
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }


@mcp.tool()
async def ssh_cmd_output(
        handle_id: Annotated[int, Field(description="Command handle ID - the 'id' field from ssh_cmd_run's response")],
        lines: Annotated[Optional[int], Field(description="How many lines to return. Without start_line: the most recent N lines (default 50). With start_line: N lines from there (default 50).")] = None,
        stream: Annotated[Literal['stdout', 'stderr'], Field(description="Which captured stream to retrieve - stdout (default) or stderr. These are NOT interleaved into one combined stream - call this twice (once per stream) if you need both")] = 'stdout',
        start_line: Annotated[Optional[int], Field(description="Page through the output: return lines starting at this line number (1 = the command's first line). Use it to read what a truncated ssh_cmd_run response didn't include (see its output_note).", ge=1)] = None
) -> list:
    """
    Retrieve captured output from a command started with ssh_cmd_run, identified by
    its handle_id. Useful after an io_timeout/wait_timeout to see progress so far -
    including output produced after that call returned, since background monitoring
    keeps collecting it - to page through output a response didn't include in full
    (ssh_cmd_run returns at most the last ~32 KB of each stream inline and says so via
    output_truncated/output_note), or to re-inspect an earlier command's output
    without rerunning it.

    stdout and stderr are captured in separate buffers, not interleaved - `stream`
    picks which one to retrieve.

    The server keeps up to ~2 MB of each stream per command; beyond that the EARLIEST
    lines are dropped. Asking for a dropped line with start_line gives an error naming
    the first line still available. For very large output, redirect it to a file (or
    use ssh_task_launch) and read that instead.

    Raises an error if handle_id doesn't exist (e.g. from a previous connection -
    handles don't survive reconnects).

    Returns:
        A plain list of output lines from the selected stream - not a dict. For total
        line counts and whether anything was dropped, use ssh_cmd_check_status
        (output_lines) or ssh_cmd_history.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    if handle_id in _operations:
        raise SshError(f"handle_id={handle_id} is an operation ({_operations[handle_id].tool}), not "
                       f"a shell command - get its result with ssh_cmd_check_status(handle_id={handle_id}).")

    try:
        if start_line is not None:
            return mcp.ssh_client.output(handle_id, mode='chunk', start=start_line - 1,
                                         n=lines or 50, stream=stream)
        return mcp.ssh_client.output(handle_id, lines=lines, stream=stream)
    except Exception as e:
        logger.error(f"Failed to retrieve output: {e}")
        raise


@mcp.tool()
async def ssh_cmd_clear_history() -> dict:
    """
    Clear the command history for the current SSH connection, including finished
    long-running operations (ones still running stay, so they can still be polled).
    
    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        cleared_count = mcp.ssh_client.history_manager.clear()
        # Finished long-running operations are part of history too; running ones stay
        # so they can still be polled
        for op_id, op in list(_operations.items()):
            if op.done.is_set():
                _operations.pop(op_id, None)
                cleared_count += 1

        return {
            'status': 'success',
            'message': f"Command history cleared ({cleared_count} entries removed)",
            'cleared_entries': cleared_count
        }
    except Exception as e:
        logger.error(f"Failed to clear command history: {e}")
        raise

@mcp.tool()
async def ssh_cmd_history(
        limit: Annotated[Optional[int], Field(description="Number of history entries to return", ge=1)] = None,
        include_output: Annotated[bool, Field(description="Include command output snippets")] = False,
        output_lines: Annotated[int, Field(description="Number of output lines to include (0 for none)", ge=0)] = 3,
        reverse: Annotated[bool, Field(description="Return in reverse order (newest first)")] = False,
        pattern: Annotated[Optional[str], Field(description="Filter commands containing this pattern")] = None,
        include_internal: Annotated[bool, Field(description="Include internal/plumbing commands issued by other tools "
            "(e.g. ssh_file_write's sudo mv/chown/chmod dance, ssh_conn_connect's OS-detection probe, "
            "ssh_conn_verify_sudo's sudo check, ssh_dir_transfer's temp-archive handling). Set False to see "
            "only commands the user directly asked for via ssh_cmd_run/ssh_task_launch.")] = True
) -> list:
    """
    Retrieve command execution history with optional output snippets.

    Returns:
        List of dictionaries containing command history, ordered from oldest to newest by default.
        Each entry contains:
        - id: Command handle ID
        - command: Executed command
        - exit_code: Exit status
        - start_time: Execution start timestamp
        - end_time: Execution end timestamp
        - origin: 'user' for a directly user-requested command, or an internal-plumbing label
          ('tool_internal', 'connection_probe', 'sudo_probe') for a helper command issued by another tool
        - parent_tool: Name of the MCP tool that triggered this command, when origin != 'user'
        - output: Stdout snippet (if include_output=True) - stderr is NOT included here, even for
          a failed command; see the separate 'stderr' field
        - stderr: Stderr snippet (if include_output=True) - often the more useful stream for a
          failed command; retrieved the same way ssh_cmd_output(stream='stderr') would
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        history = mcp.ssh_client.history()
        # Long-running tool operations (handed off, or still running) - see operation_tool
        history = sorted(history + [op.history_entry() for op in list(_operations.values())],
                         key=lambda entry: entry.get('start_ts') or '')

        # Filter by pattern if specified
        if pattern is not None:
            history = [entry for entry in history if pattern in entry.get('cmd', '')]

        # Filter out internal/plumbing commands unless explicitly requested
        if not include_internal:
            history = [entry for entry in history if entry.get('origin', 'user') in ('user', 'operation')]

        # Apply limit if specified
        if limit is not None:
            history = history[-limit:]

        # Reverse if requested
        if reverse:
            history = history[::-1]

        results = []
        for entry in history:
            history_entry = {
                'id': entry.get('id'),
                'command': entry.get('cmd'),
                'exit_code': entry.get('exit_code'),
                'start_time': entry.get('start_ts'),
                'end_time': entry.get('end_ts'),
                'pid': entry.get('pid'),
                'origin': entry.get('origin', 'user'),
                'parent_tool': entry.get('parent_tool')
            }

            if include_output:
                try:
                    history_entry['output'] = mcp.ssh_client.output(entry['id'], lines=output_lines)
                except Exception as e:
                    history_entry['output'] = f"Unable to retrieve output: {str(e)}"
                try:
                    history_entry['stderr'] = mcp.ssh_client.output(entry['id'], lines=output_lines, stream='stderr')
                except Exception as e:
                    history_entry['stderr'] = f"Unable to retrieve output: {str(e)}"

            results.append(history_entry)

        return results
    except Exception as e:
        logger.error(f"Failed to retrieve command history: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_task_launch(
        command: Annotated[str, Field(description="Command to execute in the background")],
        use_sudo: Annotated[bool, Field(description="Run command with sudo")] = False,
        stdout_log: Annotated[
            Optional[str], Field(description="Path to redirect stdout (default: /tmp/task-<pid>.log on Linux/macOS, C:\\Windows\\Temp\\task-<pid>.log on Windows)")] = None,
        stderr_log: Annotated[
            Optional[str], Field(description="Path to redirect stderr (default: same file as stdout on Linux/macOS; a sibling <name>_err.log on Windows). The response's stderr_log always gives the real path.")] = None,
        log_output: Annotated[bool, Field(description="Whether to log output to files")] = True
) -> dict:
    """
    Launch a command in the background and return its PID immediately, without waiting for it
    to complete.

    Prefer this over ssh_cmd_run for commands that will take a long time or may be quiet for
    extended periods: package installs, container/image pulls, large downloads, backups,
    compilation. It avoids holding a blocking tool call open and the ambiguity of io_timeout -
    the PID it returns survives reconnects and can be checked anytime with ssh_task_status(pid),
    and stdout_log/stderr_log can be read with ssh_file_read while the task is still running.

    Output is redirected to files (see stdout_log/stderr_log), not captured in memory - read the
    log files to see progress or final output.

    Windows targets: the command runs under cmd.exe (CMD syntax); for PowerShell use
    powershell -NoProfile -Command "...".

    On Linux/macOS the launch FAILS with an error (nothing is started) if a log file can't be
    created, or, with use_sudo, if sudo itself fails - so a returned PID always means the task
    was really started. With use_sudo, logs in a directory only root can write are created and
    written as root.

    Returns:
        `{'command', 'pid', 'start_time', 'stdout_log', 'stderr_log'}`. Both are the paths
        the output really goes to on the remote host (read them with ssh_file_read), or
        `None` for a stream that's discarded - both are `None` if `log_output=False`, and
        `stdout_log` is `None` if you passed only `stderr_log`. With neither passed, the
        default is `/tmp/task-<pid>.log` on Linux/macOS (both streams in one file), and
        `C:\\Windows\\Temp\\task-<pid>.log` plus `task-<pid>_err.log` on Windows.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        # Don't add tasks to command history
        handle = mcp.ssh_client.launch(command, use_sudo, stdout_log, stderr_log, log_output, add_to_history=False)
        return {
            'command': command,
            'pid': handle.pid,
            'start_time': handle.start_ts.isoformat() if handle.start_ts else None,
            # Paths as they really exist remotely - None for a discarded stream
            'stdout_log': handle.stdout_log,
            'stderr_log': handle.stderr_log
        }
    except Exception as e:
        logger.error(f"Task launch failed: {e}")
        raise


# ===================
# Dir Operation Tools
# ===================

@mcp.tool()
@operation_tool
async def ssh_dir_mkdir(
    path: Annotated[str, Field(description="Directory path to create")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    mode: Annotated[int, Field(description="Directory permissions (octal)", ge=0, le=0o777)] = 0o755
) -> dict:
    """
    Create a directory on the remote system.

    Parent-directory creation and `mode` behavior differ by platform/sudo:
    - Linux/macOS, `use_sudo=False` (default): uses SFTP `mkdir`, which is NOT
      recursive - this FAILS if the parent directory doesn't already exist. `mode`
      applies to the created directory.
    - Linux/macOS, `use_sudo=True`: uses `mkdir -p -m <mode>`, which DOES create
      any missing parent directories.
    - Windows: always creates missing parent directories (`New-Item -Force`,
      regardless of `use_sudo`, which has no effect on Windows anyway). The `mode`
      parameter is IGNORED entirely on Windows - there's no equivalent to Unix
      octal permissions there.

    Returns:
        `{'status': 'success', 'path', 'mode' (octal string - reflects the
        requested mode even on Windows, where it was actually ignored), 'message',
        'connection'}` on success. Raises an exception on failure (e.g. missing
        parent directory without sudo on Linux/macOS).
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        mcp.ssh_client.mkdir(path, use_sudo, mode)
        return {
            'status': 'success',
            'path': path,
            'mode': f"{mode:o}",
            'message': f"Created directory {path} with mode {mode:o}",
            'connection': _connection_metadata()
        }
    except Exception as e:
        logger.error(f"Failed to create directory: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_remove(
    path: Annotated[str, Field(description="Directory path to remove")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    recursive: Annotated[bool, Field(description="Remove directory and contents recursively")] = False
) -> dict:
    """
    Remove a directory on the remote system - this is the simple `rmdir`/`Remove-Item`
    equivalent. There is NO dry-run/preview mode here (unlike ssh_dir_delete below) -
    it acts immediately. If `recursive=False` (default) and the directory is not
    empty, this RAISES an exception rather than returning an error dict - it does not
    partially delete anything.

    For a safer recursive delete with a preview step, use ssh_dir_delete instead,
    which defaults to `dry_run=True` and returns a graceful `{'status': 'error', ...}`
    on failure instead of raising.

    Returns:
        `{'status': 'success', 'path', 'recursive', 'message', 'connection'}` on
        success. Raises an exception on failure (non-empty directory with
        `recursive=False`, path not found, permission denied, etc.) rather than
        returning an error dict.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        mcp.ssh_client.rmdir(path, use_sudo, recursive)
        return {
            'status': 'success',
            'path': path,
            'recursive': recursive,
            'message': f"Removed directory {path}" + (" recursively" if recursive else ""),
            'connection': _connection_metadata()
        }
    except Exception as e:
        logger.error(f"Failed to remove directory: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_list_files_basic(
    path: Annotated[str, Field(description="Directory path to list")]
) -> list:
    """
    List the immediate contents of a directory (via SFTP - not recursive, no
    metadata). For recursive listing with size/permissions/type/etc., or to filter
    by filename pattern, use ssh_dir_list_advanced or ssh_dir_search_glob instead.

    Returns:
        List of bare filenames (strings) directly inside `path` - not full paths,
        not recursive, and no indication of which entries are files vs. directories
        (use ssh_file_stat on an entry, or ssh_dir_list_advanced, if you need that).
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        files = mcp.ssh_client.listdir(path)
        return files
    except Exception as e:
        logger.error(f"Failed to list directory: {e}")
        raise


# ===================
# File Operation Tools
# ===================


@mcp.tool()
@operation_tool
async def ssh_file_stat(
    path: Annotated[str, Field(description="File or directory path to get information about")]
) -> dict:
    """
    Get status information about a file or directory (via SFTP stat - works the same
    way on all platforms, no shell command involved).

    On Windows, `mode`/`uid`/`gid` come from the SFTP subsystem's own cross-platform
    attribute reporting, not real Windows ACLs/ownership - Windows has no equivalent
    concept, so these values (e.g. `uid`/`gid` of `0`) are not meaningful there and
    should not be relied on to reason about actual Windows permissions/ownership.
    `type`/`size`/`atime`/`mtime` are unaffected and accurate on all platforms.

    Returns:
        `{'exists': True, 'path', 'type' ('file'/'directory'/'symlink'/'unknown'),
        'mode' (octal string, e.g. "0o40755"), 'uid', 'gid', 'size' (bytes), 'atime',
        'mtime'}` when the path exists. `atime`/`mtime` are raw numeric Unix
        timestamps (seconds since epoch, as returned by SFTP), not formatted date
        strings - convert with `datetime.fromtimestamp()` if you need a readable date.
        If the path does NOT exist (or stat failed for another reason, e.g.
        permission denied), returns `{'exists': False, 'path', 'error'}` instead -
        this is a normal, non-exceptional return value, not a raised error.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        # SshClient.stat() itself returns SFTPAttributes object from Paramiko
        # or raises an error (e.g., IOError for not found / permission denied)
        sftp_attrs = mcp.ssh_client.stat(path)

        mode_val = sftp_attrs.st_mode
        file_type = "unknown"
        if stat_module.S_ISDIR(mode_val):
            file_type = "directory"
        elif stat_module.S_ISREG(mode_val):
            file_type = "file"
        elif stat_module.S_ISLNK(mode_val):
            file_type = "symlink"
        # Could add S_ISCHR, S_ISBLK, S_ISFIFO, S_ISSOCK if needed

        return {
            "exists": True,
            "path": path,
            "type": file_type,
            "mode": oct(mode_val), # e.g., "0o40755" for drwxr-xr-x
            "uid": sftp_attrs.st_uid,
            "gid": sftp_attrs.st_gid,
            "size": sftp_attrs.st_size,
            "atime": sftp_attrs.st_atime, # Unix timestamp
            "mtime": sftp_attrs.st_mtime, # Unix timestamp
        }
    except IOError as e:
        # errno.ENOENT is 2 (os.strerror(2) is 'No such file or directory').
        # Check if this IOError means "No such file or directory".
        if hasattr(e, 'errno') and e.errno == errno.ENOENT:
            logger.debug(f"File not found for stat({path}) (ENOENT): {e}")
            return {"exists": False, "path": path, "error": "File or directory not found."}
        # Paramiko also sometimes just puts "No such file" in the message without specific errno
        elif "no such file" in str(e).lower():
            logger.debug(f"File not found for stat({path}) (text match): {e}")
            return {"exists": False, "path": path, "error": "File or directory not found."}
        else:
            # Other IOErrors (e.g., permission denied on stat itself)
            logger.error(f"IOError getting file status for {path}: {e}")
            return {"exists": False, "path": path, "error": f"Permission denied or other IOError: {str(e)}"}
    except Exception as e: # Catch-all for other unexpected errors
        logger.error(f"Unexpected error in ssh_file_stat for {path}: {e} (type: {type(e).__name__})")
        return {"exists": False, "path": path, "error": f"Unexpected error: {str(e)}"}


@mcp.tool()
@operation_tool
async def ssh_file_read(
    file_path: Annotated[str, Field(description="Path to the file to read")],
    encoding: Annotated[str, Field(description="Character encoding (default: utf-8)")] = "utf-8",
    max_size: Annotated[int, Field(description="Maximum file size in bytes (default: 10MB, 0 for no limit)", ge=0)] = 10 * 1024 * 1024
) -> dict:
    """
    Read file contents directly via SFTP.

    This tool reads raw bytes from the remote file using SFTP and decodes them
    on the client side. Unlike command-based file reading (cat, Get-Content),
    SFTP completely bypasses shell and console encoding issues.

    **Why use this instead of ssh_cmd_run with cat/Get-Content?**
    - Works correctly with Unicode on ALL platforms including Windows
    - Bypasses Windows PowerShell's OEM code page encoding problem
    - More efficient for binary-safe file transfer
    - No shell escaping issues with special characters in content

    Returns:
        Dictionary with:
        - success: True if file was read successfully
        - content: The file contents as a string
        - size: Number of bytes read
        - encoding: The encoding used to decode the content
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        content = mcp.ssh_client.read_file(file_path, encoding, max_size)
        return {
            'success': True,
            'file_path': file_path,
            'content': content,
            'size': len(content.encode(encoding)),
            'encoding': encoding
        }
    except SshError as e:
        logger.error(f"Failed to read file {file_path}: {e}")
        return {
            'success': False,
            'file_path': file_path,
            'error': str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected error reading file {file_path}: {e}")
        return {
            'success': False,
            'file_path': file_path,
            'error': str(e)
        }


@mcp.tool()
@operation_tool
async def ssh_file_find_lines_with_pattern(
    file_path: Annotated[str, Field(description="Path to the file to search")],
    pattern: Annotated[str, Field(description="Text or regex pattern to search for")],
    regex: Annotated[bool, Field(description="Whether to treat pattern as a regular expression")] = False,
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Search for a pattern in a remote file and return matching lines with their line
    numbers. Use this to find WHERE a pattern occurs before using
    ssh_file_get_context_around_line to see surrounding lines, or
    ssh_file_replace_line/ssh_file_delete_line_by_content to edit (those require an
    exact, unique line match - this tool helps you find that exact line first).

    Regex flavor differs by platform when `regex=True`: POSIX extended regex
    (`grep -E`) on Linux/macOS - avoid PCRE-only syntax like `\\d`, use `[0-9]` or
    `[[:digit:]]` instead; Python's `re` module on Windows (matched locally after
    an SFTP read, not via PowerShell - see below). When `regex=False` (default),
    the pattern is matched as a literal fixed string on every platform.

    On Windows, this reads the whole file via SFTP and matches locally in Python,
    rather than shelling out to PowerShell/Select-String - matched line content
    could otherwise come back corrupted for non-ASCII text, since Windows' console
    encodes stdout in its OEM code page rather than UTF-8 (the same problem
    ssh_file_read's SFTP approach avoids).

    Returns:
        `{'total_matches': int, 'matches': [{'line_number': int, 'content': str}, ...]}`.
        No matches is not an error - `total_matches` is 0 and `matches` is `[]`. On
        failure (e.g. file not found, permission denied), an `'error'` key is present
        instead.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        return mcp.ssh_client.find_lines_with_pattern(file_path, pattern, regex, use_sudo)
    except Exception as e:
        logger.error(f"Failed to search file: {e}")
        raise

@mcp.tool()
@operation_tool
async def ssh_file_get_context_around_line(
    file_path: Annotated[str, Field(description="Path to the file")],
    match_line: Annotated[str, Field(description="Exact line content to match")],
    context: Annotated[int, Field(description="Number of lines before and after to include", ge=0)] = 3,
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Get lines before and after a line, to see it in context before editing. Like the
    line-editing tools (ssh_file_replace_line and siblings), `match_line` must match
    exactly one line in the file (whitespace-trimmed, literal text, not a pattern) -
    use ssh_file_find_lines_with_pattern first if you're not sure the line is unique.

    Reads the file via SFTP and matches locally (same as ssh_file_find_lines_with_pattern) -
    Unicode/non-ASCII content is safe on all platforms, including Windows.

    Returns:
        On a unique match: `{'match_found': True, 'match_line_number': int,
        'context_block': [{'line_number': int, 'content': str}, ...]}` (the matched
        line plus `context` lines before/after).
        If the line isn't found, or matches more than once: `{'match_found': False,
        'error': str}` - for a multi-match error, also includes `'matches'` (every
        matching line, so you can pick a more specific `match_line`).
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        return mcp.ssh_client.get_context_around_line(file_path, match_line, context, use_sudo)
    except Exception as e:
        logger.error(f"Failed to get context: {e}")
        raise

@mcp.tool()
@operation_tool
async def ssh_file_replace_line(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match and replace")],
    new_line: Annotated[str, Field(description="New line to insert in place of the match")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="No longer needed (kept for compatibility): with use_sudo=true the file is read with sudo when the user can't read it. If it can't be read at all, the edit fails - it never reports success without having seen the file.")] = False
) -> dict:
    """
    Replace a line in a file with a new line. `match_line` must match EXACTLY ONE
    line in the file (whitespace-trimmed, literal text - not a pattern); if it
    matches zero lines or more than one, the operation fails with a descriptive
    error rather than guessing which line you meant. Use
    ssh_file_find_lines_with_pattern first if you're not sure the line is unique.

    PARAMETERS:
    * file_path: Path to the file to modify
    * match_line: Exact line content to match and replace (whitespace-trimmed)
    * new_line: New line to insert in place of the match
    * use_sudo: Use sudo for the operation (default: false)
    * force: No longer needed (kept for compatibility) - use_sudo reads root-only files with sudo (default: false)

    RETURNS:
    On success: `{'success': True, 'lines_written': 1}` (or, in the rare edge case
    where `new_line` is identical to the matched line, `{'success': True, 'message':
    'No changes needed...'}` instead - nothing to write). On failure (match not
    found, match not unique, file not found/unreadable): `{'success': False, 'error':
    str}` - not a raised exception. Note: unlike some other file tools, this does NOT
    return `file_path` in the response.

    EXAMPLES:
    Example 1: Replace a commented line with an active configuration
    ```json
    {
      "file_path": "/etc/ssh/sshd_config",
      "match_line": "#ClientAliveInterval 0",
      "new_line": "ClientAliveInterval 300"
    }
    ```

    Note: To delete a line entirely, use the dedicated ssh_file_delete_line_by_content tool instead.
    To replace/insert MULTIPLE lines in one call, use ssh_file_replace_line_multi instead.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        # Convert the single line to a list as required by the underlying method
        new_lines = [new_line]

        result = mcp.ssh_client.replace_line_by_content(file_path, match_line, new_lines, use_sudo, force)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to replace line: {e}")
        raise


# Define a Pydantic model for the new_lines parameter
class NewLinesModel(BaseModel):
    """Pydantic model to handle the new_lines parameter for file line replacement."""
    lines: List[str]
    
    @classmethod
    def parse(cls, value: Union[List[str], str]) -> List[str]:
        """
        Parse the new_lines parameter, handling various input formats.
        
        Args:
            value: Can be a list of strings, a JSON string representing a list,
                  or a single string to be treated as a one-element list.
                  
        Returns:
            A properly formatted list of strings.
        """
        if isinstance(value, list):
            return value
        
        if isinstance(value, str):
            import json
            try:
                # Try to parse as JSON
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
                else:
                    # If it's valid JSON but not a list, wrap it in a list
                    return [str(parsed)]
            except json.JSONDecodeError:
                # If it's not valid JSON, treat it as a single string
                return [value]
        
        # For any other type, convert to string and wrap in a list
        return [str(value)]


@mcp.tool()
@operation_tool
async def ssh_file_replace_line_multi(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match and replace")],
    new_lines: Annotated[list, Field(description="List of new lines to insert in place of the match")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="No longer needed (kept for compatibility): with use_sudo=true the file is read with sudo when the user can't read it. If it can't be read at all, the edit fails - it never reports success without having seen the file.")] = False
) -> dict:
    """
    Replace a line in a file with one or more new lines (or delete it, with an empty
    list). Use this instead of ssh_file_replace_line when you need to insert more
    than one line, or delete a line without using the separate
    ssh_file_delete_line_by_content tool.

    `match_line` must match EXACTLY ONE line in the file (whitespace-trimmed, literal
    text - not a pattern); if it matches zero lines or more than one, the operation
    fails with a descriptive error rather than guessing. Use
    ssh_file_find_lines_with_pattern first if you're not sure the line is unique.

    PARAMETERS:
    * file_path: Path to the file to modify
    * match_line: Exact line content to match and replace (whitespace-trimmed)
    * new_lines: List of new lines to insert in place of the match
      - To replace with multiple lines: use ["first line", "second line", ...]
      - To delete the line entirely: use [] (empty list)
      - To replace with an empty line: use [""]
    * use_sudo: Use sudo for the operation (default: false)
    * force: No longer needed (kept for compatibility) - use_sudo reads root-only files with sudo (default: false)

    RETURNS:
    On success: `{'success': True, 'lines_written': <len(new_lines)>}` (or, in the
    rare edge case where the result is byte-identical to the original file,
    `{'success': True, 'message': 'No changes needed...'}` instead). On failure
    (match not found, match not unique, file not found/unreadable): `{'success':
    False, 'error': str}` - not a raised exception. Note: does NOT return `file_path`
    in the response.

    EXAMPLES:
    Example 1: Replace a line with multiple lines
    ```json
    {
      "file_path": "/etc/hosts",
      "match_line": "127.0.0.1 localhost",
      "new_lines": ["127.0.0.1 localhost", "127.0.0.1 myhost.local"]
    }
    ```

    Example 2: Delete a line entirely
    ```json
    {
      "file_path": "/etc/nginx/nginx.conf",
      "match_line": "# server_tokens off;",
      "new_lines": []
    }
    ```

    Example 3: Replace with an empty line
    ```json
    {
      "file_path": "/etc/ssh/sshd_config",
      "match_line": "PermitRootLogin yes",
      "new_lines": [""]
    }
    ```
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        # Use the Pydantic model to parse and validate the new_lines parameter
        parsed_new_lines = NewLinesModel.parse(new_lines)
        logger.info(f"Processed new_lines parameter: {parsed_new_lines}")

        result = mcp.ssh_client.replace_line_by_content(file_path, match_line, parsed_new_lines, use_sudo, force,
                                                          parent_tool='ssh_file_replace_line_multi')
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to replace line: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_file_transfer(
        direction: Annotated[Literal['upload', 'download'], Field(description="Transfer direction")],
        local_path: Annotated[str, Field(description="Local file path")],
        remote_path: Annotated[str, Field(description="Remote file path")],
        use_sudo: Annotated[bool, Field(description="Use sudo for transfer")] = False
) -> dict:
    """
    Transfer a single FILE between the local machine (running this MCP server) and
    the remote host, via SFTP. Both `local_path` and `remote_path` must be file
    paths, not directories - for whole-directory transfers, use ssh_dir_transfer
    instead. For remote-to-remote copies (no local machine involved), use
    ssh_file_copy instead.

    Caveat: `use_sudo=True` on download/upload stages a copy through `/tmp/` using
    Unix shell commands (`mv`/`chmod`/`rm`) - this only works against Linux/macOS
    remote hosts. Windows has no per-command sudo concept anyway (see
    ssh_conn_verify_sudo), so `use_sudo=True` against a Windows connection raises
    immediately instead of attempting these Unix commands - connect as
    Administrator instead.

    Returns:
        `{'operation' (human-readable description of what happened), 'success':
        True, 'local_path', 'remote_path', 'sudo', 'connection'}`. Raises an
        exception on failure rather than returning a `success: False` dict.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    if use_sudo and mcp.ssh_client.os_type == 'windows':
        raise SshError(
            "use_sudo is not applicable on Windows - connect as Administrator instead. "
            "This tool's sudo staging path uses Unix-only shell commands (mv/chmod/rm)."
        )

    try:
        if direction == 'upload':
            # For upload with sudo, we need to use a different approach
            if use_sudo:
                # Upload to a temporary location first
                temp_remote_path = f"/tmp/ssh_transfer_{os.path.basename(remote_path)}_{int(time.time())}"
                report_progress(stage='uploading file')
                mcp.ssh_client.put(local_path, temp_remote_path)
                
                # Then move it to the final location with sudo
                move_cmd = f"mv {shlex.quote(temp_remote_path)} {shlex.quote(remote_path)}"
                mcp.ssh_client.run(move_cmd, sudo=True)
                operation = f"Uploaded {local_path} to {remote_path} with sudo"
            else:
                report_progress(stage='uploading file')
                mcp.ssh_client.put(local_path, remote_path)
                operation = f"Uploaded {local_path} to {remote_path}"
        else:  # download
            # For download with sudo, we need to use a different approach
            if use_sudo:
                # Copy to a temporary location with sudo
                temp_remote_path = f"/tmp/ssh_transfer_{os.path.basename(remote_path)}_{int(time.time())}"
                copy_cmd = f"cp {shlex.quote(remote_path)} {shlex.quote(temp_remote_path)}"
                mcp.ssh_client.run(copy_cmd, sudo=True)
                
                # Make it readable
                chmod_cmd = f"chmod 644 {shlex.quote(temp_remote_path)}"
                mcp.ssh_client.run(chmod_cmd, sudo=True)
                
                # Download from the temporary location
                report_progress(stage='downloading file')
                mcp.ssh_client.get(temp_remote_path, local_path)
                
                # Clean up
                rm_cmd = f"rm -f {shlex.quote(temp_remote_path)}"
                mcp.ssh_client.run(rm_cmd, sudo=True)
                
                operation = f"Downloaded {remote_path} to {local_path} with sudo"
            else:
                report_progress(stage='downloading file')
                mcp.ssh_client.get(remote_path, local_path)
                operation = f"Downloaded {remote_path} to {local_path}"

        return {
            'operation': operation,
            'success': True,
            'local_path': local_path,
            'remote_path': remote_path,
            'sudo': use_sudo,
            'connection': _connection_metadata()
        }
    except Exception as e:
        logger.error(f"File transfer failed: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_transfer(
        direction: Annotated[Literal['upload', 'download'], Field(description="Transfer direction")],
        local_path: Annotated[str, Field(description="Local directory path")],
        remote_path: Annotated[str, Field(description="Remote directory path")],
        use_sudo: Annotated[bool, Field(description="Use sudo for remote operations")] = False
) -> dict:
    """
    Transfer directories between local and remote systems.

    Uses archive-based transfer for efficiency:
    - Upload: Archives locally, transfers, extracts on remote
    - Download: Archives on remote, transfers, extracts locally

    Archive format is automatically selected based on remote OS:
    - Linux/macOS: tar.gz
    - Windows: zip

    Where the files end up (check `files_location` in the result):
    - upload: the CONTENTS of local_path are placed directly in remote_path
      (local /x/proj/a.txt -> remote_path/a.txt)
    - download: the remote folder itself is placed inside local_path
      (remote /srv/proj/a.txt -> local_path/proj/a.txt)

    What it returns (a dictionary):
        - success: whether the transfer succeeded
        - operation: 'upload' or 'download'
        - local_path, remote_path: as given
        - files_location: the folder where the transferred files now are
        - archive_format: 'tar.gz' or 'zip'
        - files_transferred: number of regular files (folders not counted)
        - directories: number of subfolders (the top folder itself not counted)
        - payload_bytes: total size of the transferred files
        - archive_bytes: size of the compressed archive actually sent
        - bytes_transferred: same as archive_bytes (kept for compatibility)

    Args:
        direction: 'upload' (local to remote) or 'download' (remote to local)
        local_path: Local directory path
        remote_path: Remote directory path
        use_sudo: Use sudo for remote archive/extract operations (Linux/macOS only)
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        result = mcp.ssh_client.transfer_directory(
            direction=direction,
            local_path=local_path,
            remote_path=remote_path,
            sudo=use_sudo
        )
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Directory transfer failed: {e}")
        raise


#
@mcp.tool()
@operation_tool
async def ssh_file_insert_lines_after_match(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match")],
    lines_to_insert: Annotated[list, Field(description="Line(s) to insert after the match")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="No longer needed (kept for compatibility): with use_sudo=true the file is read with sudo when the user can't read it. If it can't be read at all, the edit fails - it never reports success without having seen the file.")] = False
) -> dict:
    """
    Insert one or more new lines immediately after a matching line. `match_line`
    must match EXACTLY ONE line in the file (whitespace-trimmed, literal text - not
    a pattern); if it matches zero lines or more than one, the operation fails with
    a descriptive error rather than guessing. Use ssh_file_find_lines_with_pattern
    first if you're not sure the line is unique.

    PARAMETERS:
    * file_path: Path to the file to modify
    * match_line: Exact line content to match (whitespace-trimmed)
    * lines_to_insert: List of lines to insert after the match
      - To insert multiple lines: use ["first line", "second line", ...]
      - To insert a single line: use ["line to insert"]
      - To insert an empty line: use [""]
    * use_sudo: Use sudo for the operation (default: false)
    * force: No longer needed (kept for compatibility) - use_sudo reads root-only files with sudo (default: false)

    RETURNS:
    On success: `{'success': True, 'lines_inserted': <len(lines_to_insert)>}` (note:
    the key is `lines_inserted` here, vs. `lines_written` on
    ssh_file_replace_line/ssh_file_replace_line_multi). On failure (match not found,
    match not unique, file not found/unreadable): `{'success': False, 'error': str}`
    - not a raised exception. Does NOT return `file_path` in the response.

    EXAMPLES:
    Example 1: Insert configuration lines after a marker
    ```json
    {
      "file_path": "/etc/nginx/nginx.conf",
      "match_line": "http {",
      "lines_to_insert": ["    server_tokens off;", "    client_max_body_size 20M;"]
    }
    ```

    Example 2: Add a new host entry after localhost
    ```json
    {
      "file_path": "/etc/hosts",
      "match_line": "127.0.0.1 localhost",
      "lines_to_insert": ["192.168.1.10 myserver.local"]
    }
    ```
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        # Use the Pydantic model to parse and validate the lines_to_insert parameter
        parsed_lines_to_insert = NewLinesModel.parse(lines_to_insert)
        logger.info(f"Processed lines_to_insert parameter: {parsed_lines_to_insert}")

        result = mcp.ssh_client.insert_lines_after_match(file_path, match_line, parsed_lines_to_insert, use_sudo, force)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to insert lines: {e}")
        raise

@mcp.tool()
@operation_tool
async def ssh_file_delete_line_by_content(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match and delete")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="No longer needed (kept for compatibility): with use_sudo=true the file is read with sudo when the user can't read it. If it can't be read at all, the edit fails - it never reports success without having seen the file.")] = False
) -> dict:
    """
    Delete a line by its exact content. `match_line` must match EXACTLY ONE line in
    the file (whitespace-trimmed, literal text - not a pattern); if it matches zero
    lines or more than one, the operation fails with a descriptive error rather than
    guessing which line(s) to delete. Use ssh_file_find_lines_with_pattern first if
    you're not sure the line is unique. (Equivalent to
    ssh_file_replace_line_multi(new_lines=[]), provided as a clearer-named shortcut.)

    Returns:
        On success: `{'success': True}` (no count field - only ever deletes the one
        matched line). On failure (match not found, match not unique, file not
        found/unreadable): `{'success': False, 'error': str}` - not a raised
        exception.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        result = mcp.ssh_client.delete_line_by_content(file_path, match_line, use_sudo, force)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to delete line: {e}")
        raise

@mcp.tool()
@operation_tool
async def ssh_file_copy(
    source_path: Annotated[str, Field(description="Source file path")],
    destination_path: Annotated[str, Field(description="Destination file path")],
    append_timestamp: Annotated[bool, Field(description="Whether to append a timestamp to the destination")] = False,
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Copy a file on the remote host (source and destination are both remote paths -
    for local<->remote transfers use ssh_file_transfer instead).

    If `append_timestamp=True`, a timestamp is inserted before the destination's file
    extension in the format `%Y%m%dT%H%M%S`, e.g. `destination_path="/etc/hosts.bak"`
    becomes `/etc/hosts.20260704T153045.bak` - not appended after the extension, and
    not configurable to a different format.

    Returns:
        On success: `{'success': True, 'copied_to': <actual destination path used,
        including the timestamp if applied>}`. On failure (source not found,
        permission error): `{'success': False, 'error': str}` - not a raised
        exception.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        result = mcp.ssh_client.copy_file(source_path, destination_path, append_timestamp, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to copy file: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_file_write(
        file_path: Annotated[str, Field(description="Path to the file to write to")],
        content: Annotated[str, Field(description="Content to write to the file")],
        append: Annotated[bool, Field(description="Whether to append to the file instead of overwriting")] = False,
        use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
        mode: Annotated[Optional[int], Field(description="File permissions to set after writing (octal, e.g. 0o644)")] = None,
        create_dirs: Annotated[bool, Field(description="Create parent directories if they don't exist")] = False
) -> dict:
    """
    Create a new file, or overwrite/append to an existing one, with the given
    content (works the same whether or not the file already exists - `append=True`
    on a nonexistent file just creates it). Handles special characters and
    multi-line content properly.

    `mode` (Unix permission bits) is applied via a `chmod` command after writing -
    this only works on Linux/macOS. On Windows there's no equivalent, so `mode` is
    silently ignored there (same convention as `ssh_dir_mkdir`'s `mode` parameter).

    Returns:
        On success: `{'success': True, 'file_path', 'bytes_written' (int), 'mode'
        (octal string, or `None` if not set OR if the connection is Windows - the
        response never echoes back a mode value that wasn't actually applied),
        'append' (bool, echoes the parameter), 'connection'}`. On failure (e.g.
        parent directory missing and `create_dirs=False`, write error):
        `{'success': False, 'file_path', 'error'}` - not a raised exception.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        # Create a local temporary file with the content
        # Ensure we use Unix-style line endings (LF) for consistency
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, newline='\n', encoding='utf-8') as temp_file:
            temp_file.write(content)
            local_temp_path = temp_file.name
        
        try:
            original_meta = None  # set below for an existing file written with sudo
            # Create parent directories first if requested (before any file operations)
            if create_dirs:
                parent_dir = os.path.dirname(file_path)
                if parent_dir:
                    try:
                        # Create all parent directories recursively
                        is_windows = mcp.ssh_client.os_type == 'windows'
                        if is_windows:
                            # Windows: use PowerShell New-Item with -Force (creates all parent directories)
                            ps_path = parent_dir.replace("'", "''")
                            mkdir_cmd = powershell_encoded_command(f"New-Item -ItemType Directory -Force -Path '{ps_path}' | Out-Null")
                        else:
                            # Linux/macOS: use mkdir -p
                            mkdir_cmd = f"mkdir -p {shlex.quote(parent_dir)}"

                        if use_sudo and not is_windows:
                            mcp.ssh_client.run(mkdir_cmd, sudo=True, origin='tool_internal', parent_tool='ssh_file_write')
                        else:
                            mcp.ssh_client.run(mkdir_cmd, origin='tool_internal', parent_tool='ssh_file_write')
                        logger.info(f"Created parent directories for {file_path}")
                    except Exception as e:
                        # Ignore if directory already exists
                        if "File exists" not in str(e) and "already exists" not in str(e).lower():
                            logger.error(f"Failed to create parent directories for {file_path}: {e}")
                            raise
            
            try:
                # Check if parent directory exists when create_dirs is False
                if not create_dirs:
                    parent_dir = os.path.dirname(file_path)
                    try:
                        with mcp.ssh_client._client.open_sftp() as sftp:
                            sftp.stat(parent_dir)
                    except FileNotFoundError:
                        logger.error(f"Parent directory {parent_dir} does not exist and create_dirs=False")
                        return {
                            'success': False,
                            'file_path': file_path,
                            'error': f"Parent directory does not exist: {parent_dir}. Use create_dirs=True to create it."
                        }
                
                # An existing file's owner/group/mode, read with sudo before writing, so a
                # sudo write can restore them - it used to chown every sudo-written file to
                # the connected user, silently exposing root-only files (2026-09-28).
                original_meta = None
                if use_sudo and mcp.ssh_client.os_type != 'windows':
                    try:
                        meta_cmd = mcp.ssh_client.file_ops._cmd_stat_permissions(file_path)
                        meta = mcp.ssh_client.run(meta_cmd, sudo=True, io_timeout=15,
                                                  origin='tool_internal', parent_tool='ssh_file_write')
                        parts = meta.last_nonblank().split()
                        if len(parts) == 3 and all(part.isdigit() for part in parts):
                            original_meta = parts  # [octal perms, uid, gid]
                    except Exception:
                        original_meta = None  # no such file yet (or no stat): a new file

                if use_sudo:
                    # For sudo operations, we need to use a different approach
                    # First, create a temporary file in a location we can write to
                    is_windows = mcp.ssh_client.os_type == 'windows'
                    if is_windows:
                        # Windows: use Windows temp directory
                        base_name = os.path.basename(file_path).replace('\\', '_').replace(':', '_')
                        remote_temp_path = f"C:\\Windows\\Temp\\ssh_file_write_{base_name}_{int(time.time())}"
                    else:
                        remote_temp_path = f"/tmp/ssh_file_write_{os.path.basename(file_path)}_{int(time.time())}"

                    # Upload to the temporary location first
                    mcp.ssh_client.put(local_temp_path, remote_temp_path)

                    if is_windows:
                        # Windows: use PowerShell Copy-Item (use_sudo is ignored on Windows - admin already has permissions)
                        if not append:
                            copy_cmd = f"Copy-Item -Path '{remote_temp_path}' -Destination '{file_path}' -Force"
                        else:
                            copy_cmd = f"Get-Content -Path '{remote_temp_path}' | Add-Content -Path '{file_path}'"
                        mcp.ssh_client.run(powershell_encoded_command(copy_cmd), origin='tool_internal', parent_tool='ssh_file_write')
                        # Clean up temp file
                        mcp.ssh_client.run(powershell_encoded_command(f"Remove-Item -Path '{remote_temp_path}' -Force -ErrorAction SilentlyContinue"),
                                            origin='tool_internal', parent_tool='ssh_file_write')
                    else:
                        if not append:
                            # For overwrite with sudo, use cat with sudo redirection
                            cat_cmd = f"cat {shlex.quote(remote_temp_path)} > {shlex.quote(file_path)}"
                            mcp.ssh_client.run(f"sh -c {shlex.quote(cat_cmd)}", sudo=True, origin='tool_internal', parent_tool='ssh_file_write')
                        else:
                            # For append with sudo, use cat with sudo append redirection
                            cat_cmd = f"cat {shlex.quote(remote_temp_path)} >> {shlex.quote(file_path)}"
                            mcp.ssh_client.run(f"sh -c {shlex.quote(cat_cmd)}", sudo=True, origin='tool_internal', parent_tool='ssh_file_write')
                        # Clean up the temporary file
                        mcp.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}", origin='tool_internal', parent_tool='ssh_file_write')
                elif not append:
                    # For overwrite without sudo, simply upload the file
                    mcp.ssh_client.put(local_temp_path, file_path)
                else:
                    # For append, we need to check if the file exists first
                    try:
                        # Check if file exists using SFTP
                        try:
                            mcp.ssh_client.stat(file_path)
                            file_exists = True
                        except IOError:
                            file_exists = False
                        
                        if file_exists:
                            # File exists, so we need to append
                            if use_sudo:
                                # This case is now handled in the sudo block above
                                pass
                            else:
                                # For non-sudo append, download, append locally, then upload
                                with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8') as combined_file:
                                    combined_path = combined_file.name
                                    
                                try:
                                    # Download existing file
                                    mcp.ssh_client.get(file_path, combined_path)
                                    
                                    # Append new content with Unix-style line endings
                                    with open(combined_path, 'a', newline='\n', encoding='utf-8') as f:
                                        f.write(content)
                                    
                                    # Upload combined file
                                    mcp.ssh_client.put(combined_path, file_path)
                                finally:
                                    if os.path.exists(combined_path):
                                        os.unlink(combined_path)
                        else:
                            # File doesn't exist, so just create it
                            if not use_sudo:  # sudo case is handled above
                                mcp.ssh_client.put(local_temp_path, file_path)
                    except Exception as e:
                        # If any error occurs during append, fall back to simple upload
                        logger.warning(f"Error during append operation, falling back to create: {e}")
                        if use_sudo:
                            # For sudo, we need to use the sudo approach
                            is_windows = mcp.ssh_client.os_type == 'windows'
                            if is_windows:
                                base_name = os.path.basename(file_path).replace('\\', '_').replace(':', '_')
                                remote_temp_path = f"C:\\Windows\\Temp\\ssh_file_write_{base_name}_{int(time.time())}"
                                mcp.ssh_client.put(local_temp_path, remote_temp_path)
                                copy_cmd = f"Copy-Item -Path '{remote_temp_path}' -Destination '{file_path}' -Force"
                                mcp.ssh_client.run(powershell_encoded_command(copy_cmd), origin='tool_internal', parent_tool='ssh_file_write')
                                mcp.ssh_client.run(powershell_encoded_command(f"Remove-Item -Path '{remote_temp_path}' -Force -ErrorAction SilentlyContinue"),
                                                    origin='tool_internal', parent_tool='ssh_file_write')
                            else:
                                remote_temp_path = f"/tmp/ssh_file_write_{os.path.basename(file_path)}_{int(time.time())}"
                                mcp.ssh_client.put(local_temp_path, remote_temp_path)
                                cat_cmd = f"cat {shlex.quote(remote_temp_path)} > {shlex.quote(file_path)}"
                                mcp.ssh_client.run(f"sh -c {shlex.quote(cat_cmd)}", sudo=True, origin='tool_internal', parent_tool='ssh_file_write')
                                mcp.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}", origin='tool_internal', parent_tool='ssh_file_write')
                        else:
                            mcp.ssh_client.put(local_temp_path, file_path)
            except FileNotFoundError as e:
                if "No such file" in str(e) and create_dirs:
                    # This is likely because the parent directory doesn't exist yet
                    # We already tried to create it, but let's try again with a more direct approach
                    logger.warning(f"Directory creation may have failed, retrying with direct command")
                    parent_dir = os.path.dirname(file_path)
                    if parent_dir:
                        is_windows = mcp.ssh_client.os_type == 'windows'
                        if is_windows:
                            ps_path = parent_dir.replace("'", "''")
                            mkdir_cmd = powershell_encoded_command(f"New-Item -ItemType Directory -Force -Path '{ps_path}' | Out-Null")
                        else:
                            mkdir_cmd = f"mkdir -p {shlex.quote(parent_dir)}"
                        if use_sudo and not is_windows:
                            mcp.ssh_client.run(mkdir_cmd, sudo=True, origin='tool_internal', parent_tool='ssh_file_write')
                        else:
                            mcp.ssh_client.run(mkdir_cmd, origin='tool_internal', parent_tool='ssh_file_write')
                        logger.info(f"Created parent directories for {file_path}")

                        # Now try the upload again
                        if not append:
                            mcp.ssh_client.put(local_temp_path, file_path)
                        else:
                            # For a new file with append=True, just create it
                            mcp.ssh_client.put(local_temp_path, file_path)
                else:
                    # If not related to directory creation or create_dirs is False, return error
                    logger.error(f"SFTP put failed: {e}")
                    return {
                        'success': False,
                        'file_path': file_path,
                        'error': f"SFTP put failed: {str(e)}"
                    }
            
            # Set file permissions if specified (no-op on Windows, which has no chmod)
            if mode is not None and mcp.ssh_client.os_type != 'windows':
                chmod_cmd = f"chmod {mode:o} {shlex.quote(file_path)}"
                mcp.ssh_client.run(chmod_cmd, sudo=use_sudo, origin='tool_internal', parent_tool='ssh_file_write')

            # Ownership after a sudo write (not applicable on Windows)
            if use_sudo and mcp.ssh_client.os_type != 'windows':
                if original_meta is not None:
                    # Existing file: keep its own owner/group, and its mode unless one was given
                    perms, uid, gid = original_meta
                    fixups = [f"chown {uid}:{gid} {shlex.quote(file_path)}"]
                    if mode is None:
                        fixups.append(f"chmod {perms} {shlex.quote(file_path)}")
                    for fixup in fixups:
                        try:
                            mcp.ssh_client.run(fixup, sudo=True, origin='tool_internal', parent_tool='ssh_file_write')
                        except Exception as e:
                            logger.warning(f"Failed to restore ownership/mode of {file_path} ({fixup}): {e}")
                else:
                    # New file: owned by the connected user (existing, documented behavior)
                    whoami_result = mcp.ssh_client.run("whoami", origin='tool_internal', parent_tool='ssh_file_write')
                    current_user = whoami_result.get_full_output().strip()
                    if current_user and current_user != "root":
                        chown_cmd = f"chown {current_user} {shlex.quote(file_path)}"
                        try:
                            mcp.ssh_client.run(chown_cmd, sudo=True, origin='tool_internal', parent_tool='ssh_file_write')
                        except Exception as e:
                            logger.warning(f"Failed to set ownership of {file_path}: {e}")
            
            # Get file size for reporting
            try:
                sftp_attrs = mcp.ssh_client.stat(file_path)
                file_size = sftp_attrs.st_size
            except IOError:
                file_size = len(content)
            
            # mode is silently ignored on Windows (no chmod there - see the guard
            # above) - report None rather than echoing back a value that was never
            # actually applied, which would misleadingly look like it took effect.
            reported_mode = f"{mode:o}" if (mode is not None and mcp.ssh_client.os_type != 'windows') else None

            return {
                'success': True,
                'file_path': file_path,
                'bytes_written': file_size,
                'mode': reported_mode,
                'append': append,
                'connection': _connection_metadata()
            }
        finally:
            # Clean up the temporary file
            if os.path.exists(local_temp_path):
                os.unlink(local_temp_path)
                
    except Exception as e:
        logger.error(f"Failed to write to file {file_path}: {e}")
        return {
            'success': False,
            'file_path': file_path,
            'error': str(e)
        }

@mcp.tool()
@operation_tool
async def ssh_file_move(
        source: Annotated[str, Field(description="Source file or directory path")],
        destination: Annotated[str, Field(description="Destination path")],
        overwrite: Annotated[bool, Field(description="Overwrite destination if it exists")] = False,
        use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Move or rename a file or directory (works for both - whichever `source` is).

    If `destination` already exists: fails cleanly with `overwrite=False` (default);
    with `overwrite=True`, it's replaced (`mv -f`/`Move-Item -Force`). If `source`
    doesn't exist, also fails cleanly rather than raising.

    Returns:
        `{'success': True, 'message': str}` on success, or `{'success': False,
        'message': str}` on failure (source not found, destination exists and
        `overwrite=False`, permission error) - not a raised exception.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        result = mcp.ssh_client.safe_move_or_rename(source, destination, overwrite, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to move file/directory: {e}")
        raise


# ===========================
# Directory Operation Tools
# ===========================

@mcp.tool()
@operation_tool
async def ssh_dir_search_glob(
    path: Annotated[str, Field(description="Base directory to search from")],
    pattern: Annotated[str, Field(description="Filename glob pattern (e.g. *.log)")],
    max_depth: Annotated[Optional[int], Field(description="Maximum recursion depth (None for unlimited)", ge=1)] = None,
    include_dirs: Annotated[bool, Field(description="Include matching directories in results")] = False,
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> list:
    """
    Recursively search for files (or directories, with `include_dirs=True`) matching
    a filename glob pattern (e.g. `*.log`) - matches by NAME only, not content; use
    ssh_dir_search_files_content instead to search inside files. For metadata beyond
    just path/type (size, mtime, permissions), use ssh_dir_list_advanced instead.

    `max_depth` uses standard `find -maxdepth` semantics: this is the same on both
    Linux/macOS and Windows despite the different underlying commands. `max_depth=1`
    means the given `path` itself plus its immediate children only (not
    grandchildren); omit `max_depth` for unlimited recursion.

    On a 'linux'/'flex' connection with a limited BusyBox-style `find` (no
    `-printf` - check `ssh_conn_connect`/`ssh_conn_host_info`'s `capabilities`),
    this raises a clear error instead of running. Fallback: `ssh_dir_list_files_basic`
    (non-recursive filenames, match the pattern yourself) - SFTP-based and
    unaffected by this, at the cost of one call per directory level.

    Returns:
        List of `{'path': str, 'type': str}` - `type` is a single-character code
        (`f`=file, `d`=directory, `l`=symlink) from the underlying `find`/`stat`
        output, not a spelled-out word.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        # Check the signature of search_files_recursive and pass only the arguments it accepts
        report_progress(stage='searching file names')
        results = mcp.ssh_client.search_files_recursive(path, pattern, max_depth, include_dirs)
        return results
    except Exception as e:
        logger.error(f"Failed to search files: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_calc_size(
    path: Annotated[str, Field(description="Directory path to calculate size for")]
) -> dict:
    """
    Calculate the total size of a directory recursively: the sum of the sizes of all
    regular files under it (directories' own sizes are not counted - same on every
    platform).

    On a 'linux' connection whose `find` lacks `-printf` (check `ssh_conn_connect`/
    `ssh_conn_host_info`'s `capabilities`), this raises a clear error instead of running.
    Fallback: `ssh_cmd_run("du -sk <path>")` - kilobytes, and it includes directory
    overhead, but works on most BusyBox builds.

    Returns:
        `{'path', 'size_bytes' (int), 'size_human' (e.g. "1.23 MB", "512.00 KB",
        "3.50 GB" - 2 decimal places, binary/1024-based units)}`.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        report_progress(stage='calculating size')
        size_bytes = mcp.ssh_client.calculate_directory_size(path)
        return {
            'path': path,
            'size_bytes': size_bytes,
            'size_human': _format_size(size_bytes)
        }
    except Exception as e:
        logger.error(f"Failed to calculate directory size: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_delete(
    path: Annotated[str, Field(description="Directory path to delete")],
    dry_run: Annotated[bool, Field(description="Preview deletion without actually deleting")] = True,
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Delete a directory and all its contents recursively. `dry_run` DEFAULTS TO
    `True` - calling this with no arguments other than `path` only PREVIEWS what
    would be deleted and deletes NOTHING; you must explicitly pass `dry_run=False`
    to actually delete. Refuses to delete a small set of critical paths outright
    (root, home directory, `C:\\Windows`, `C:\\Users`, etc.) regardless of `dry_run`.

    For a simpler non-recursive removal without the preview step, see ssh_dir_remove.

    Returns:
        `{'status': 'success'/'error', 'deleted_items': [str, ...] (paths that
        were/would be removed, depth-first order)}`, plus `'dry_run': True` ONLY when
        this was a preview (check for this key to tell a preview apart from a real
        deletion - it's absent, not `False`, on actual deletions) and `'error'`
        (string) on failure. Also fails (with `'error'` set) if `path` is a
        recognized critical directory.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        result = mcp.ssh_client.delete_directory_recursive(path, dry_run, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to delete directory: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_batch_delete_files(
    path: Annotated[str, Field(description="Base directory to search in")],
    pattern: Annotated[str, Field(description="File pattern to match for deletion (e.g. *.tmp)")],
    dry_run: Annotated[bool, Field(description="Preview deletion without actually deleting")] = True,
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Recursively find and delete files matching a filename glob pattern (same pattern
    syntax as ssh_dir_search_glob, e.g. `*.tmp`) under a directory. `dry_run`
    DEFAULTS TO `True` - calling this with no arguments other than `path`/`pattern`
    only PREVIEWS which files would be deleted and deletes NOTHING; you must
    explicitly pass `dry_run=False` to actually delete.

    On a 'linux'/'flex' connection with a BusyBox-style `xargs` that doesn't
    support `-0` (check `ssh_conn_connect`/`ssh_conn_host_info`'s `capabilities`),
    this raises a clear error instead of running. Fallback: `ssh_cmd_run` with
    `find <path> -name '<pattern>' -exec rm -f {} +` instead - portable, and
    still safe with spaces in filenames.

    Returns:
        `{'status': 'success'/'error', 'deleted_files': [str, ...] (matching file
        paths that were/would be deleted)}`, plus `'dry_run': True` ONLY when this
        was a preview (check for this key to tell a preview apart from a real
        deletion) and `'error'` (string) on failure. Note the key is `deleted_files`
        here, vs. `deleted_items` on ssh_dir_delete.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        result = mcp.ssh_client.batch_delete_by_pattern(path, pattern, dry_run, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to batch delete files: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_list_advanced(
    path: Annotated[str, Field(description="Directory path to list")],
    max_depth: Annotated[Optional[int], Field(description="Maximum recursion depth (None for unlimited)", ge=1)] = None,
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> list:
    """
    List contents of a directory recursively with full metadata (size, permissions,
    ownership, modification time) - use this instead of ssh_dir_list_files_basic
    (which only returns bare filenames, non-recursive) when you need more than just
    names, or ssh_dir_search_glob when you only need to filter by filename pattern
    without full metadata (that tool is also faster for large trees).

    `max_depth` uses standard `find -maxdepth` semantics: `max_depth=1` means `path`
    itself plus its immediate children only; omit for unlimited recursion.

    On a 'linux'/'flex' connection with a limited BusyBox-style `find` (no
    `-printf` - check `ssh_conn_connect`/`ssh_conn_host_info`'s `capabilities`),
    this raises a clear error instead of running. Fallback: `ssh_dir_list_files_basic`
    (non-recursive filenames) plus `ssh_file_stat` per entry for metadata - both are
    SFTP-based and unaffected by this, at the cost of one call per directory level.

    Returns:
        List of `{'path', 'type', 'size_bytes' (int), 'modified_time' (float Unix
        timestamp), 'permissions' (string, e.g. "755"), 'user', 'group'}`. `type` is
        a spelled-out word here ('file'/'directory'/'symlink'/'pipe'/'socket'/
        'block'/'character') - note this differs from ssh_dir_search_glob, which
        returns single-character type codes ('f'/'d'/'l') for the same concept.
        On Windows, `permissions` is always the literal placeholder `"0"` (Windows
        has no Unix-style permission bits) and `group` is always `"unknown"` -
        `user` still reflects the real file owner there.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        results = mcp.ssh_client.list_directory_recursive(path, max_depth, use_sudo)
        return results
    except Exception as e:
        logger.error(f"Failed to list directory: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_search_files_content(
        dir_path: Annotated[str, Field(description="Directory to search in")],
        pattern: Annotated[str, Field(description="Text or pattern to search for")],
        regex: Annotated[bool, Field(description="Treat pattern as regular expression")] = False,
        case_sensitive: Annotated[bool, Field(description="Perform case-sensitive search")] = True,
        use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> list:
    """
    Recursively search file CONTENTS for a pattern under a directory (unlike
    ssh_dir_search_glob/ssh_file_find_lines_with_pattern, which match filenames or
    search within a single already-known file respectively).

    Regex flavor differs by platform when `regex=True`: POSIX extended regex
    (`grep -E`) on Linux/macOS - avoid PCRE-only syntax like `\\d`, use `[0-9]` or
    `[[:digit:]]` instead; Python's `re` module on Windows. When `regex=False`
    (default), the pattern is matched as a literal fixed string.

    On Windows the whole search runs over SFTP (directory listings + raw file reads,
    matched locally), so non-ASCII file names and content are handled correctly.

    Returns:
        List of `{'file': str, 'line': int, 'content': str}` - one entry per matching
        line, across all files under `dir_path`. Empty list if nothing matches (not
        an error) - but only when every file could be searched.

        If some files or subdirectories could NOT be searched (unreadable, or on
        Windows larger than 10 MB), the result is instead
        `{'status': 'incomplete', 'matches': [...same entries...], 'skipped': [{'path',
        'reason'}, ...], 'skipped_count': int, 'note': str}` - so "no matches" is never
        claimed for files that weren't actually searched. A missing or unreadable
        `dir_path` itself is an error.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        report_progress(stage='searching file contents')
        results = mcp.ssh_client.search_file_contents(dir_path, pattern, regex, case_sensitive, use_sudo)
        skipped = getattr(mcp.ssh_client.dir_ops, 'last_search_skipped', None) or []
        if skipped:
            shown = skipped[:50]
            return {
                'status': 'incomplete',
                'matches': results,
                'skipped': shown,
                'skipped_count': len(skipped),
                'note': (f"{len(skipped)} file(s)/folder(s) under {dir_path} could not be searched "
                         f"(see 'skipped'{', first 50 shown' if len(skipped) > 50 else ''}), so "
                         f"'matches' may be missing results from them. The other files were "
                         f"searched normally."),
            }
        return results
    except Exception as e:
        logger.error(f"Failed to search file contents: {e}")
        raise


@mcp.tool()
@operation_tool
async def ssh_dir_copy(
        source_path: Annotated[str, Field(description="Source directory path")],
        destination_path: Annotated[str, Field(description="Destination directory path")],
        overwrite: Annotated[bool, Field(description="Overwrite existing files")] = False,
        preserve_symlinks: Annotated[bool, Field(description="Preserve symbolic links")] = True,
        preserve_permissions: Annotated[bool, Field(description="Preserve file permissions")] = True,
        use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Copy a directory recursively (remote-to-remote; for local<->remote use
    ssh_dir_transfer instead).

    `overwrite` controls what happens if `destination_path` already exists:
    - `True`: the entire existing destination directory is deleted first, then a
      fresh copy is made.
    - `False` (default): does NOT block the copy - it copies into the existing
      destination as-is, merging with whatever's already there, and any file that
      shares a name with a source file gets silently overwritten anyway. This does
      NOT behave like the "fail if destination exists" semantics of
      ssh_file_move/ssh_file_copy's own `overwrite` parameter.

    Returns:
        `{'status': 'success'/'error', 'files_copied' (int), 'bytes_copied' (int),
        'destination_path'}` (plus `'message'` on error). Note: `files_copied`/
        `bytes_copied` are computed by counting the destination directory's TOTAL
        contents *after* the copy, not just the files newly copied in this call - if
        merging into a non-empty destination, these counts include pre-existing files too.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())

    try:
        report_progress(stage='copying')
        result = mcp.ssh_client.copy_directory_recursive(
            source_path, destination_path, overwrite, preserve_symlinks, preserve_permissions, use_sudo
        )
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to copy directory: {e}")
        raise


# ===========================
# Archive Operation Tools
# ===========================

@mcp.tool()
@operation_tool
async def ssh_archive_create(
    source_path: Annotated[str, Field(description="Directory to archive")],
    archive_path: Annotated[str, Field(description="Path for the created archive")],
    format: Annotated[Literal["tar.gz", "tar"], Field(description="Archive format")] = "tar.gz",
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Create a compressed archive from a directory.

    IMPORTANT cross-platform caveat: on Windows, `tar.gz`/`tar` are NOT natively
    available - requesting either one silently creates a `.zip` file instead (via
    `Compress-Archive`), and `archive_path`'s extension is auto-corrected to `.zip`
    if needed. The only way to tell this happened is the returned `format` field
    saying `'zip'` even though you asked for `tar.gz`/`tar`. An archive created this
    way on Windows can only be extracted with ssh_archive_extract on another Windows
    host - Linux/macOS's extractor only recognizes `.tar`/`.tar.gz`/`.tgz` files, not
    `.zip`. There is no cross-platform-portable archive format currently available
    through this tool.

    Returns:
        On success: `{'status': 'success', 'success': True, 'archive_created' (the
        actual path used, which may differ from `archive_path` if the extension was
        corrected on Windows), 'format' (the ACTUAL format used - 'tar.gz'/'tar' on
        Linux/macOS, always 'zip' on Windows), 'size_bytes'}`. On failure:
        `{'status': 'error', 'message': str}` - not a raised exception.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        report_progress(stage='creating archive')
        result = mcp.ssh_client.create_archive_from_directory(source_path, archive_path, format, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to create archive: {e}")
        raise


_EXTRACT_SAMPLE_SIZE = 50


def _summarize_extraction(result, strip_top_level):
    """Keep the ssh_archive_extract result concise: counts plus a short sample of file
    paths (relative to the destination), instead of every extracted name - an
    18,000-file archive used to produce ~300 KB of JSON, which clients like OpenCode
    can't show inline. The files themselves are on disk and can be listed any time."""
    entries = [e.replace('\\', '/') for e in result.get('extracted_files') or []]
    if strip_top_level:
        # tar's listing includes the archive's top folder, which extraction strips
        # (--strip-components=1); make paths relative to the destination, like Windows
        entries = [e.split('/', 1)[1] if '/' in e else '' for e in entries]
    files = [e for e in entries if e and not e.endswith('/')]
    directories = {e.rstrip('/') for e in entries if e.endswith('/') and e.rstrip('/')}
    for path in files:
        parts = path.split('/')[:-1]
        directories.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))
    summary = {
        **{k: v for k, v in result.items() if k != 'extracted_files'},
        'files_extracted': len(files),
        'directories': len(directories),
        'extracted_files': files[:_EXTRACT_SAMPLE_SIZE],
        'extracted_files_truncated': len(files) > _EXTRACT_SAMPLE_SIZE,
    }
    if len(files) > _EXTRACT_SAMPLE_SIZE:
        summary['note'] = (f"extracted_files shows the first {_EXTRACT_SAMPLE_SIZE} of "
                           f"{len(files)} files. List them all with "
                           f"ssh_dir_search_glob(path='{result.get('destination_path')}', pattern='*').")
    if result.get('existing_files_kept'):
        summary['note'] = (summary.get('note', '') + " Some files already existed in the destination "
                           "and were kept, not overwritten (overwrite=False).").strip()
    return summary


@mcp.tool()
@operation_tool
async def ssh_archive_extract(
    archive_path: Annotated[str, Field(description="Path to the archive file")],
    destination_path: Annotated[str, Field(description="Directory to extract to")],
    overwrite: Annotated[bool, Field(description="Overwrite existing files")] = False,
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Extract an archive to a directory. The accepted format is platform-specific and
    determined purely by `archive_path`'s file extension (not by inspecting the
    archive's actual content):
    - Linux/macOS: `.tar.gz`, `.tgz`, or `.tar` only. A `.zip` file (e.g. created by
      ssh_archive_create on Windows) will fail here with an unsupported-format error.
    - Windows: `.zip` only. A `.tar`/`.tar.gz` file (e.g. created by
      ssh_archive_create on Linux/macOS) will fail here the same way.
    There is no cross-platform-portable archive format currently available through
    this tool - archives must be created and extracted on the same OS family.

    `overwrite=False` (default) does not fail outright if some files already exist at
    the destination - on Linux/macOS it extracts everything else and just logs a
    warning about the skipped files (no per-file detail returned); on Windows the
    behavior follows `Expand-Archive`'s own overwrite handling.

    On a 'linux'/'flex' connection with a BusyBox-style `tar` (check
    `ssh_conn_connect`/`ssh_conn_host_info`'s `capabilities`), this can raise a
    clear error instead of running - unlike the wrong-format/extraction-error
    cases below, which return an error dict, this is a raised exception. Two
    independent gaps: missing `--strip-components` blocks extraction entirely
    (no clean single-tool fallback - extract without stripping via `ssh_cmd_run`,
    the archive's own top-level directory will remain, then relocate its
    contents up one level yourself); missing `--keep-old-files` only blocks when
    `overwrite` isn't explicitly `True` - passing `overwrite=True` avoids that
    one specifically.

    Returns:
        On success: `{'status': 'success', 'success': True, 'destination_path',
        'files_extracted' (number of files), 'directories' (number of folders),
        'extracted_files' (the first 50 file paths, relative to destination_path),
        'extracted_files_truncated' (True if there are more), 'note' (how to list them all,
        and whether existing files were kept because overwrite=False)}`. On failure:
        `{'status': 'error', 'message': str, 'extracted_files': []}` - not a raised exception.
    """
    if not mcp.ssh_client:
        raise SshError(_not_connected_message())
        
    try:
        report_progress(stage='extracting archive')
        result = mcp.ssh_client.extract_archive_to_directory(archive_path, destination_path, overwrite, use_sudo)
        if result.get('status') == 'success':
            result = _summarize_extraction(result, strip_top_level=mcp.ssh_client.os_type != 'windows')
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to extract archive: {e}")
        raise


# ===================
# Helper Functions
# ===================

def _format_size(size_bytes):
    """Format bytes into human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

# ===================
# Main Execution
# ===================

def main():
    """Entry point for CLI execution."""
    global host_manager, _default_host_manager, max_foreground_wait

    # Parse command line arguments
    args = parse_args()

    if args.max_wait is not None:
        max_foreground_wait = args.max_wait if args.max_wait > 0 else None
    _apply_output_limits(args)

    # Re-initialize host manager with config path if provided
    host_manager = SshHostManager(
        config_path=Path(args.config) if args.config else None
    )
    _default_host_manager = host_manager

    try:
        logger.info(f"Starting SSH MCP server '{mcp.name}' ")
        logger.info(f"Using TOML config file: {host_manager.config_path}")
        logger.info(f"ssh_cmd_run foreground wait cap: "
                    f"{f'{max_foreground_wait:g}s' if max_foreground_wait else 'disabled'}")
        logger.info(f"Output limits: {OutputLimits.per_stream} bytes kept per command/stream, "
                    f"{OutputLimits.inline} returned inline, {OutputLimits.total} total")
        logger.info("Available tools (can be retrieved programmatically via 'list_tools' tool):")
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by user (KeyboardInterrupt)")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Server crashed with error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
