import logging
import sys
import os
import yaml
import argparse
import asyncio
from pathlib import Path
from fastmcp import FastMCP
from pydantic import Field
from typing import Annotated, Optional, Literal, Dict
from datetime import datetime, UTC
from ssh_client import SshClient
from ssh_models import SshError, CommandTimeout, CommandRuntimeTimeout, CommandFailed, SudoRequired, BusyError


def parse_args():
    parser = argparse.ArgumentParser(description="SSH MCP Server")
    parser.add_argument(
        '--config',
        type=str,
        help="Path to SSH hosts configuration file",
        default=None
    )
    return parser.parse_args()



class SshHostManager:
    def __init__(self, config_path: Optional[Path] = None):
        # Try paths in this order:
        # 1. Explicit config_path if provided
        # 2. ~/.ssh_hosts.yaml
        # 3. ./ssh_hosts.yaml
        if config_path:
            self.config_path = config_path
        else:
            home_config = Path.home() / ".ssh_hosts.yaml"
            project_config = Path("ssh_hosts.yaml")
            
            if home_config.exists():
                self.config_path = home_config
            else:
                self.config_path = project_config
                
        self._ensure_config_file()
        self.hosts = self._load_hosts()

    def _ensure_config_file(self):
        """Create config file if it doesn't exist with secure permissions."""
        if not self.config_path.exists():
            with open(self.config_path, 'w') as f:
                yaml.safe_dump({'hosts': []}, f)
            self.config_path.chmod(0o600)  # rw-------

    def _load_hosts(self) -> Dict[str, Dict]:
        """Load hosts from config file."""
        try:
            with open(self.config_path, 'r') as f:
                data = yaml.safe_load(f) or {}
            return {h['name']: h for h in data.get('hosts', [])}
        except Exception as e:
            logger.error(f"Failed to load SSH hosts: {e}")
            return {}

    def get_host(self, name: str) -> Optional[Dict]:
        """Get host config by name."""
        return self.hosts.get(name)

    def add_host(self, name: str, host: str, port: int, user: str, password: str):
        """Add or update a host configuration."""
        self.hosts[name] = {
            'name': name,
            'host': host,
            'port': port,
            'user': user,
            'password': password
        }
        self._save_hosts()

    def _save_hosts(self):
        """Save hosts to config file."""
        try:
            with open(self.config_path, 'w') as f:
                yaml.safe_dump({'hosts': list(self.hosts.values())}, f)
            self.config_path.chmod(0o600)  # Maintain secure permissions
        except Exception as e:
            logger.error(f"Failed to save SSH hosts: {e}")
            raise SshError("Failed to save host configuration")

# Only parse command line arguments when run directly
if __name__ == "__main__":
    # Parse command line arguments
    args = parse_args()
    
    # Initialize host manager with config path if provided
    host_manager = SshHostManager(
        config_path=Path(args.config) if args.config else None
    )
else:
    # When imported as a module (e.g. during testing), use default config
    host_manager = SshHostManager()



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
        name="SSH_Management_Server",
        description="MCP server for managing SSH connections and operations",
        version="0.1.0"
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





# ====================================================
#          Core SSH Tools
# ====================================================


@mcp.tool()
async def ssh_conn_is_connected() -> bool:
    """
    Check if there is an active SSH connection.
    
    Returns:
        bool: True if an active connection exists, False otherwise.
    """
    return mcp.ssh_client is not None and mcp.ssh_client.is_connected()



@mcp.tool()
async def ssh_conn_connect(
    host_name: Annotated[str, Field(description="Name of host configuration to use")]
) -> dict:
    """
    Establish an SSH connection using a pre-configured host.
    
    Returns:
        Dictionary with connection status
    """
    try:
        host_config = host_manager.get_host(host_name)
        if not host_config:
            raise SshError(f"Host configuration '{host_name}' not found")
            
        if mcp.ssh_client:
            logger.warning("Closing existing SSH connection")
            mcp.ssh_client.close()
            
        mcp.ssh_client = SshClient(
            host=host_config['host'],
            user=host_config['user'],
            password=host_config['password'],
            port=host_config['port']
        )
        
        return {
            'status': 'success',
            'host': host_config['host'],
            'user': host_config['user']
        }
    except Exception as e:
        logger.error(f"Failed to connect to {host_name}: {e}")
        raise



@mcp.tool()
async def ssh_conn_add_host(
    name: Annotated[str, Field(description="Unique name for this host configuration")],
    host: Annotated[str, Field(description="Hostname or IP address")],
    user: Annotated[str, Field(description="Username for authentication")],
    password: Annotated[str, Field(description="Password for authentication", secret=True)],
    port: Annotated[int, Field(description="SSH port", ge=1, le=65535)] = 22
) -> dict:
    """
    Add or update a host configuration.
    
    Returns:
        Dictionary with operation status
    """
    try:
        host_manager.add_host(name, host, port, user, password)
        return {
            'status': 'success',
            'host': host,
            'user': user
        }
    except Exception as e:
        logger.error(f"Failed to add host: {e}")
        raise



@mcp.tool()
async def ssh_conn_status() -> dict:
    """
    Get current SSH connection status and system information.
    
    Returns:
        Dictionary containing connection status and system info
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        status = mcp.ssh_client.get_connection_status()
        system_info = mcp.ssh_client.full_status()
        return {
            'connection': status,
            'system': system_info
        }
    except Exception as e:
        logger.error(f"Failed to get status: {e}")
        raise


@mcp.tool()
async def ssh_conn_verify_sudo() -> bool:
    """
    Verify if password-less sudo is available on the remote system.
    
    Returns:
        True if sudo access is available, False otherwise
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.verify_sudo_access()
    except Exception as e:
        logger.error(f"Failed to verify sudo access: {e}")
        raise



# ===================
# Task Operation Tools
# ===================


@mcp.tool()
async def ssh_task_status(
    pid: Annotated[int, Field(description="Process ID to check status for")]
) -> dict:
    """
    Check the status of a background task by PID.
    
    Returns:
        Dictionary containing task status information
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        status = mcp.ssh_client.task_status(pid)
        return {
            'pid': pid,
            'status': status,
            'running': status == 'running',
            'timestamp': datetime.now(UTC).isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get task status: {e}")
        raise


@mcp.tool()
async def ssh_task_kill(
    pid: Annotated[int, Field(description="Process ID to terminate")],
    signal: Annotated[int, Field(description="Signal to send (15=TERM, 9=KILL)", ge=1, le=15)] = 15,
    sudo: Annotated[bool, Field(description="Use sudo for the kill operation")] = False,
    force: Annotated[bool, Field(description="Force kill with SIGKILL if process doesn't exit")] = True,
    wait_seconds: Annotated[float, Field(description="Seconds to wait before force kill", gt=0)] = 1.0
) -> dict:
    """
    Terminate a background task by sending a signal to its PID.
    
    If force=True and the process doesn't exit after wait_seconds,
    it will be forcibly killed with SIGKILL (signal 9).
    
    Returns:
        Dictionary containing kill operation result
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        force_kill_signal = 9 if force else None
        result = mcp.ssh_client.task_kill(pid, signal, sudo, force_kill_signal, wait_seconds)
        return {
            'pid': pid,
            'result': result,
            'signal': signal,
            'force_kill_used': result == 'killed' and force,
            'timestamp': datetime.now(UTC).isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to kill task: {e}")
        raise



# ===================
# Cmd Operation Tools
# ===================


@mcp.tool()
async def ssh_cmd_run(
        command: Annotated[str, Field(description="Command to execute on remote host")],
        io_timeout: Annotated[float, Field(description="I/O timeout in seconds", gt=0)] = 60.0,
        runtime_timeout: Annotated[Optional[float], Field(description="Total runtime timeout in seconds", gt=0)] = None,
        sudo: Annotated[bool, Field(description="Run command with sudo")] = False
) -> dict:
    """
    Execute a command on the remote host and return the results.

    Returns:
        Dictionary containing command output, status, and metadata.
        Status field indicates success or the type of failure (timeout, runtime_timeout, etc.)
    """
    if not mcp.ssh_client:
        return {
            'status': 'error',
            'error': "No active SSH connection",
            'command': command,
            'timestamp': datetime.now(UTC).isoformat()
        }

    try:
        handle = mcp.ssh_client.run(command, io_timeout, runtime_timeout, sudo)
        output = handle.get_full_output()
        return {
            'status': 'success',
            'id': handle.id,
            'command': command,
            'exit_code': handle.exit_code,
            'output': output,
            'pid': handle.pid,
            'start_time': handle.start_ts.isoformat(),
            'end_time': handle.end_ts.isoformat() if handle.end_ts else None
        }
    except CommandTimeout as e:
        logger.warning(f"Command I/O timeout after {e.seconds}s: {command}")
        # Get the handle from history if available
        history = mcp.ssh_client.history()
        handle = next((h for h in history if h.get('cmd') == command), None)
        handle_id = handle.get('id') if handle else None

        return {
            'status': 'io_timeout',
            'id': handle_id,
            'command': command,
            'timeout_seconds': e.seconds,
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }
    except CommandRuntimeTimeout as e:
        logger.warning(f"Command runtime timeout after {e.seconds}s: {command}")
        return {
            'status': 'runtime_timeout',
            'id': e.handle.id,
            'command': command,
            'timeout_seconds': e.seconds,
            'pid': e.handle.pid,
            'output': e.handle.get_full_output() if hasattr(e.handle, 'get_full_output') else None,
            'start_time': e.handle.start_ts.isoformat() if hasattr(e.handle, 'start_ts') else None,
            'end_time': e.handle.end_ts.isoformat() if hasattr(e.handle, 'end_ts') else None,
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }
    except CommandFailed as e:
        logger.warning(f"Command failed with exit code {e.exit_code}: {command}")
        return {
            'status': 'command_failed',
            'command': command,
            'exit_code': e.exit_code,
            'stdout': e.stdout,
            'stderr': e.stderr,
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
        return {
            'status': 'error',
            'command': command,
            'error': str(e),
            'error_type': type(e).__name__,
            'timestamp': datetime.now(UTC).isoformat()
        }




@mcp.tool()
async def ssh_cmd_kill(
    handle_id: Annotated[int, Field(description="Command handle ID to kill")],
    signal: Annotated[int, Field(description="Signal to send (15=TERM, 9=KILL)", ge=1, le=15)] = 15,
    force: Annotated[bool, Field(description="Force kill with SIGKILL if process doesn't exit")] = True,
    wait_seconds: Annotated[float, Field(description="Seconds to wait before force kill", gt=0)] = 1.0
) -> dict:
    """
    Terminate a currently running command by its handle ID.
    
    This tool is specifically for killing commands started with ssh_cmd_run,
    not background tasks launched with ssh_task_launch.
    
    If force=True and the process doesn't exit after wait_seconds,
    it will be forcibly killed with SIGKILL (signal 9).
    
    Returns:
        Dictionary containing kill operation result
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
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
            return {
                'handle_id': handle_id,
                'pid': pid,
                'result': 'not_running',
                'message': f"Command is not running (status: {status})",
                'timestamp': datetime.now(UTC).isoformat()
            }
            
        # Kill the process using the existing task_kill method
        force_kill_signal = 9 if force else None
        result = mcp.ssh_client.task_kill(pid, signal, False, force_kill_signal, wait_seconds)
        
        return {
            'handle_id': handle_id,
            'pid': pid,
            'result': result,
            'signal': signal,
            'force_kill_used': result == 'killed' and force,
            'timestamp': datetime.now(UTC).isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to kill command: {e}")
        raise



@mcp.tool()
async def ssh_cmd_check_status(
    handle_id: Annotated[int, Field(description="Command handle ID to check status for")],
    wait_seconds: Annotated[float, Field(description="Seconds to wait before checking", gt=0)] = 5.0
) -> dict:
    """
    Wait for the specified duration and then check the status of a command.
    
    This tool helps with monitoring long-running commands started with ssh_cmd_run
    by implementing a wait operation that LLMs cannot perform on their own.
     
    Returns:
        Dictionary containing command status information after waiting
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        # Log the wait operation
        logger.info(f"Waiting {wait_seconds} seconds before checking status of handle {handle_id}")
        
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
                # Command exists in history
                is_complete = handle_info.get('end_ts') is not None
                exit_code = handle_info.get('exit_code')
                
                return {
                    'handle_id': handle_id,
                    'waited_seconds': wait_seconds,
                    'status': 'completed' if is_complete else 'running',
                    'exit_code': exit_code if is_complete else None,
                    'pid': handle_info.get('pid'),
                    'timestamp': datetime.now(UTC).isoformat(),
                    'output_available': True,
                    'output_lines': len(output) if output else 0
                }
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
        handle_id: Annotated[int, Field(description="Command handle ID to retrieve output for")],
        lines: Annotated[Optional[int], Field(description="Number of lines to retrieve (None for all)")] = None
) -> list:
    """
    Retrieve output from a specific command execution.

    Returns:
        List of output lines from the command
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        return mcp.ssh_client.output(handle_id, lines=lines)
    except Exception as e:
        logger.error(f"Failed to retrieve output: {e}")
        raise



@mcp.tool()
async def ssh_cmd_history(
        limit: Annotated[Optional[int], Field(description="Number of history entries to return", ge=1)] = None,
        include_output: Annotated[bool, Field(description="Include command output snippets")] = False,
        output_lines: Annotated[int, Field(description="Number of output lines to include (0 for none)", ge=0)] = 3,
        reverse: Annotated[bool, Field(description="Return in reverse order (newest first)")] = False
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
        - output: Command output snippet (if include_output=True)
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        history = mcp.ssh_client.history()

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
                'pid': entry.get('pid')
            }

            if include_output:
                try:
                    output = mcp.ssh_client.output(entry['id'], lines=output_lines)
                    history_entry['output'] = output
                except Exception as e:
                    history_entry['output'] = f"Unable to retrieve output: {str(e)}"

            results.append(history_entry)

        return results
    except Exception as e:
        logger.error(f"Failed to retrieve command history: {e}")
        raise


@mcp.tool()
async def ssh_task_launch(
        command: Annotated[str, Field(description="Command to execute in the background")],
        sudo: Annotated[bool, Field(description="Run command with sudo")] = False,
        stdout_log: Annotated[
            Optional[str], Field(description="Path to redirect stdout (default: /tmp/task-<pid>.log)")] = None,
        stderr_log: Annotated[
            Optional[str], Field(description="Path to redirect stderr (default: same as stdout)")] = None,
        log_output: Annotated[bool, Field(description="Whether to log output to files")] = True
) -> dict:
    """
    Launch a command in the background and return its PID.

    Unlike ssh_run, this does not wait for the command to complete.
    Output is redirected to files or /dev/null, not captured in memory.

    Returns:
        Dictionary containing task information including PID
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        handle = mcp.ssh_client.launch(command, sudo, stdout_log, stderr_log, log_output)
        return {
            'command': command,
            'pid': handle.pid,
            'start_time': handle.start_ts.isoformat() if handle.start_ts else None,
            'stdout_log': stdout_log or f"/tmp/task-{handle.pid}.log" if log_output else None,
            'stderr_log': stderr_log or f"/tmp/task-{handle.pid}.log" if log_output else None
        }
    except Exception as e:
        logger.error(f"Task launch failed: {e}")
        raise


# ===================
# Dir Operation Tools
# ===================

@mcp.tool()
async def ssh_dir_mkdir(
    path: Annotated[str, Field(description="Directory path to create")],
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    mode: Annotated[int, Field(description="Directory permissions (octal)", ge=0, le=0o777)] = 0o755
) -> dict:
    """
    Create a directory on the remote system.
    
    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        mcp.ssh_client.mkdir(path, sudo, mode)
        return {
            'status': 'success',
            'path': path,
            'mode': f"{mode:o}",
            'message': f"Created directory {path} with mode {mode:o}"
        }
    except Exception as e:
        logger.error(f"Failed to create directory: {e}")
        raise



@mcp.tool()
async def ssh_dir_remove(
    path: Annotated[str, Field(description="Directory path to remove")],
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    recursive: Annotated[bool, Field(description="Remove directory and contents recursively")] = False
) -> dict:
    """
    Remove a directory on the remote system.
    
    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        mcp.ssh_client.rmdir(path, sudo, recursive)
        return {
            'status': 'success',
            'path': path,
            'recursive': recursive,
            'message': f"Removed directory {path}" + (" recursively" if recursive else "")
        }
    except Exception as e:
        logger.error(f"Failed to remove directory: {e}")
        raise



@mcp.tool()
async def ssh_dir_list_files_basic(
    path: Annotated[str, Field(description="Directory path to list")]
) -> list:
    """
    List contents of a directory on the remote system.
    
    Returns:
        List of filenames in the directory
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
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
async def ssh_file_stat(
    path: Annotated[str, Field(description="File or directory path to get information about")]
) -> dict:
    """
    Get status information about a file or directory.
    
    Returns:
        Dictionary with file/directory metadata
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        stat_info = mcp.ssh_client.stat(path)
        
        # Ensure we're returning a dictionary, not a string
        if isinstance(stat_info, str):
            # Check if it's a directory listing format (starts with permissions)
            if stat_info.startswith('d') or stat_info.startswith('-') or stat_info.startswith('l'):
                # Parse directory listing format
                parts = stat_info.split()
                if len(parts) >= 5:
                    permissions = parts[0]
                    is_dir = permissions.startswith('d')
                    size = int(parts[4]) if parts[4].isdigit() else 0
                    
                    return {
                        "exists": True,
                        "type": "directory" if is_dir else "file",
                        "permissions": permissions,
                        "size": size,
                        "raw": stat_info
                    }
            
            # Try to parse as JSON if it's a string
            try:
                import json
                parsed_info = json.loads(stat_info)
                # If parsed result is still a string, create a dict
                if isinstance(parsed_info, str):
                    return {"message": parsed_info, "exists": True}
                return parsed_info
            except json.JSONDecodeError:
                # If it's not JSON, create a simple dict with the string
                return {"message": stat_info, "exists": True}
        
        # If it's already a dict, ensure it has the 'exists' key
        if isinstance(stat_info, dict) and 'exists' not in stat_info:
            stat_info['exists'] = True
            
        return stat_info
    except Exception as e:
        logger.error(f"Failed to get file status: {e}")
        # Return a structured error response instead of raising
        return {"error": str(e), "exists": False}



#
@mcp.tool()
async def ssh_file_find_lines_with_pattern(
    file_path: Annotated[str, Field(description="Path to the file to search")],
    pattern: Annotated[str, Field(description="Text or regex pattern to search for")],
    regex: Annotated[bool, Field(description="Whether to treat pattern as a regular expression")] = False,
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Search for a pattern in a remote file and return matching lines.
    
    Returns:
        Dictionary with total matches and list of matches (line number and content)
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.find_lines_with_pattern(file_path, pattern, regex, sudo)
    except Exception as e:
        logger.error(f"Failed to search file: {e}")
        raise

@mcp.tool()
async def ssh_file_get_context_around_line(
    file_path: Annotated[str, Field(description="Path to the file")],
    match_line: Annotated[str, Field(description="Exact line content to match")],
    context: Annotated[int, Field(description="Number of lines before and after to include", ge=0)] = 3,
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Get lines before and after a line that matches exactly.
    
    Returns:
        Dictionary with match line number and context block
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.get_context_around_line(file_path, match_line, context, sudo)
    except Exception as e:
        logger.error(f"Failed to get context: {e}")
        raise

@mcp.tool()
async def ssh_file_replace_line_by_content(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match and replace")],
    new_lines: Annotated[list, Field(description="New line(s) to insert in place of the match")],
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="Force operation even if file can't be read (sudo only)")] = False
) -> dict:
    """
    Replace a unique line (by exact content) with new lines.
    
    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.replace_line_by_content(file_path, match_line, new_lines, sudo, force)
    except Exception as e:
        logger.error(f"Failed to replace line: {e}")
        raise



@mcp.tool()
async def ssh_file_transfer(
        direction: Annotated[Literal['upload', 'download'], Field(description="Transfer direction")],
        local_path: Annotated[str, Field(description="Local file path")],
        remote_path: Annotated[str, Field(description="Remote file path")],
        sudo: Annotated[bool, Field(description="Use sudo for transfer")] = False
) -> dict:
    """
    Transfer files between local and remote systems.

    Returns:
        Dictionary containing transfer status and metadata
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        if direction == 'upload':
            mcp.ssh_client.put(local_path, remote_path)
            operation = f"Uploaded {local_path} to {remote_path}"
        else:
            mcp.ssh_client.get(remote_path, local_path)
            operation = f"Downloaded {remote_path} to {local_path}"

        return {
            'operation': operation,
            'success': True,
            'local_path': local_path,
            'remote_path': remote_path
        }
    except Exception as e:
        logger.error(f"File transfer failed: {e}")
        raise

#
@mcp.tool()
async def ssh_file_insert_lines_after_match(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match")],
    lines_to_insert: Annotated[list, Field(description="Line(s) to insert after the match")],
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="Force operation even if file can't be read (sudo only)")] = False
) -> dict:
    """
    Insert lines after a unique line match.
    
    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.insert_lines_after_match(file_path, match_line, lines_to_insert, sudo, force)
    except Exception as e:
        logger.error(f"Failed to insert lines: {e}")
        raise

@mcp.tool()
async def ssh_file_delete_line_by_content(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match and delete")],
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="Force operation even if file can't be read (sudo only)")] = False
) -> dict:
    """
    Delete a line matching a unique content string.
    
    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.delete_line_by_content(file_path, match_line, sudo, force)
    except Exception as e:
        logger.error(f"Failed to delete line: {e}")
        raise

@mcp.tool()
async def ssh_file_copy(
    source_path: Annotated[str, Field(description="Source file path")],
    destination_path: Annotated[str, Field(description="Destination file path")],
    append_timestamp: Annotated[bool, Field(description="Whether to append a timestamp to the destination")] = False,
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Copy a file with optional timestamp appended to the destination.
    
    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.copy_file(source_path, destination_path, append_timestamp, sudo)
    except Exception as e:
        logger.error(f"Failed to copy file: {e}")
        raise


@mcp.tool()
async def ssh_file_move(
        source: Annotated[str, Field(description="Source file or directory path")],
        destination: Annotated[str, Field(description="Destination path")],
        overwrite: Annotated[bool, Field(description="Overwrite destination if it exists")] = False,
        sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Move or rename a file or directory.

    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        result = mcp.ssh_client.safe_move_or_rename(source, destination, overwrite, sudo)
        return result
    except Exception as e:
        logger.error(f"Failed to move file/directory: {e}")
        raise


# ===========================
# Directory Operation Tools
# ===========================

@mcp.tool()
async def ssh_dir_search_glob(
    path: Annotated[str, Field(description="Base directory to search from")],
    pattern: Annotated[str, Field(description="Filename glob pattern (e.g. *.log)")],
    max_depth: Annotated[Optional[int], Field(description="Maximum recursion depth (None for unlimited)", ge=1)] = None,
    include_dirs: Annotated[bool, Field(description="Include matching directories in results")] = False
) -> list:
    """
    Recursively search for files matching a pattern.
    
    Returns:
        List of dictionaries with file information
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        results = mcp.ssh_client.search_files_recursive(path, pattern, max_depth, include_dirs)
        return results
    except Exception as e:
        logger.error(f"Failed to search files: {e}")
        raise



@mcp.tool()
async def ssh_dir_calc_size(
    path: Annotated[str, Field(description="Directory path to calculate size for")]
) -> dict:
    """
    Calculate the total size of a directory recursively.
    
    Returns:
        Dictionary with size information
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
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
async def ssh_dir_delete(
    path: Annotated[str, Field(description="Directory path to delete")],
    dry_run: Annotated[bool, Field(description="Preview deletion without actually deleting")] = True,
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Delete a directory and all its contents recursively.
    
    Returns:
        Dictionary with deletion status and details
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.delete_directory_recursive(path, dry_run, sudo)
        return result
    except Exception as e:
        logger.error(f"Failed to delete directory: {e}")
        raise



@mcp.tool()
async def ssh_dir_batch_delete_files(
    path: Annotated[str, Field(description="Base directory to search in")],
    pattern: Annotated[str, Field(description="File pattern to match for deletion (e.g. *.tmp)")],
    dry_run: Annotated[bool, Field(description="Preview deletion without actually deleting")] = True,
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Delete all files matching a pattern under a directory.
    
    Returns:
        Dictionary with deletion status and details
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.batch_delete_by_pattern(path, pattern, dry_run, sudo)
        return result
    except Exception as e:
        logger.error(f"Failed to batch delete files: {e}")
        raise



@mcp.tool()
async def ssh_dir_list_advanced(
    path: Annotated[str, Field(description="Directory path to list")],
    max_depth: Annotated[Optional[int], Field(description="Maximum recursion depth (None for unlimited)", ge=1)] = None,
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> list:
    """
    List contents of a directory recursively with detailed information.
    
    Returns:
        List of dictionaries with file/directory information
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        results = mcp.ssh_client.list_directory_recursive(path, max_depth, sudo)
        return results
    except Exception as e:
        logger.error(f"Failed to list directory: {e}")
        raise


@mcp.tool()
async def ssh_dir_search_files_content(
        dir_path: Annotated[str, Field(description="Directory to search in")],
        pattern: Annotated[str, Field(description="Text or pattern to search for")],
        regex: Annotated[bool, Field(description="Treat pattern as regular expression")] = False,
        case_sensitive: Annotated[bool, Field(description="Perform case-sensitive search")] = True,
        sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> list:
    """
    Search for text patterns in files of given directory.

    Returns:
        List of dictionaries with search matches
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        results = mcp.ssh_client.search_file_contents(dir_path, pattern, regex, case_sensitive, sudo)
        return results
    except Exception as e:
        logger.error(f"Failed to search file contents: {e}")
        raise


@mcp.tool()
async def ssh_dir_copy(
        source_path: Annotated[str, Field(description="Source directory path")],
        destination_path: Annotated[str, Field(description="Destination directory path")],
        overwrite: Annotated[bool, Field(description="Overwrite existing files")] = False,
        preserve_symlinks: Annotated[bool, Field(description="Preserve symbolic links")] = True,
        preserve_permissions: Annotated[bool, Field(description="Preserve file permissions")] = True,
        sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Copy a directory recursively.

    Returns:
        Dictionary with copy operation details
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        result = mcp.ssh_client.copy_directory_recursive(
            source_path, destination_path, overwrite, preserve_symlinks, preserve_permissions, sudo
        )
        return result
    except Exception as e:
        logger.error(f"Failed to copy directory: {e}")
        raise


# ===========================
# Archive Operation Tools
# ===========================

@mcp.tool()
async def ssh_archive_create(
    source_path: Annotated[str, Field(description="Directory to archive")],
    archive_path: Annotated[str, Field(description="Path for the created archive")],
    format: Annotated[Literal["tar.gz", "zip"], Field(description="Archive format")] = "tar.gz",
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Create a compressed archive from a directory.
    
    Returns:
        Dictionary with archive information
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.create_archive_from_directory(source_path, archive_path, format, sudo)
        return result
    except Exception as e:
        logger.error(f"Failed to create archive: {e}")
        raise




@mcp.tool()
async def ssh_archive_extract(
    archive_path: Annotated[str, Field(description="Path to the archive file")],
    destination_path: Annotated[str, Field(description="Directory to extract to")],
    overwrite: Annotated[bool, Field(description="Overwrite existing files")] = False,
    sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> dict:
    """
    Extract an archive to a directory.
    
    Returns:
        Dictionary with extraction information
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.extract_archive_to_directory(archive_path, destination_path, overwrite, sudo)
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

if __name__ == '__main__':
    try:
        logger.info(f"Starting SSH MCP server '{mcp.name}' version {mcp.version}")
        logger.info(f"Using config file: {host_manager.config_path}")
        logger.info("Available tools:")
        for tool in mcp.list_tools():
            logger.info(f"  - {tool.name}: {tool.description}")
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by user (KeyboardInterrupt)")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Server crashed with error: {e}", exc_info=True)
        sys.exit(1)
