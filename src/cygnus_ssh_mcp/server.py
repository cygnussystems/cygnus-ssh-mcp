import logging
import sys
import os
import argparse
import asyncio
import tempfile
import shlex
import time
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
from cygnus_ssh_mcp.models import SshError, CommandTimeout, CommandRuntimeTimeout, CommandFailed, SudoRequired, BusyError, CwdNotFound
from cygnus_ssh_mcp.ps_encode import powershell_encoded_command
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
    return parser.parse_args()



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
    # mcp.get_tools() returns a dict of {tool_name: FunctionTool}
    try:
        tools_dict = await mcp.get_tools()
        for name, tool_spec in tools_dict.items():
            tool_details = {
                "name": name,
                "description": getattr(tool_spec, 'description', 'No description available.')
            }
            available_tools.append(tool_details)
    except Exception as e:
        logger.error(f"Error accessing mcp.get_tools(): {e}", exc_info=True)

    return available_tools


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
    host_name: Annotated[str, Field(description="The 'user@hostname' identifier or alias of a pre-configured host")]
) -> dict:
    """
    Establish an SSH connection using a pre-configured host.
    The host can be specified by its 'user@hostname' key or by its alias.

    Returns:
        Dictionary with connection status and detailed system information
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
            cwd_result = mcp.ssh_client.run("cd")
        else:
            cwd_result = mcp.ssh_client.run("pwd")
        cwd = cwd_result.get_full_output().strip() if cwd_result.exit_code == 0 else "Unknown"

        # Update the connection status with the current working directory
        mcp.ssh_client.update_connection_status(force=True)
        
        # Get detailed system information
        status = mcp.ssh_client.get_connection_status()
        # Update the cwd in the connection status
        status['cwd'] = cwd
        system_info = mcp.ssh_client.full_status()
        
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
async def ssh_conn_status() -> dict:
    """
    Get essential SSH connection status information.
    
    Returns:
        Dictionary containing basic connection status (user, working directory, OS type)
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        status = mcp.ssh_client.get_connection_status()

        # Get current working directory (use OS-appropriate command)
        if mcp.ssh_client.os_type == 'windows':
            cwd_result = mcp.ssh_client.run("cd")
        else:
            cwd_result = mcp.ssh_client.run("pwd")
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
async def ssh_conn_host_info() -> dict:
    """
    Get detailed SSH connection status and system information.
    
    Returns:
        Dictionary containing full connection status and detailed system info
        including hardware, memory, disk usage, and more.
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
        host = mcp.ssh_client.get_connection_status().get('host', 'unknown')
        user = mcp.ssh_client.get_connection_status().get('user', 'unknown')
        
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
        raise SshError("No active SSH connection")

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
            result = mcp.ssh_client.run("sudo -n true", io_timeout=5.0)
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
                    result = mcp.ssh_client.run("true", sudo=True, io_timeout=5.0)
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
                    result = mcp.ssh_client.run("sudo -l -U $(whoami) | grep -q '(ALL'", io_timeout=5.0)
                    requires_password = result.exit_code == 0
                except Exception as e:
                    logger.debug(f"Sudo access check failed: {e}")

                    # Try another approach - check if user is in sudo group
                    try:
                        result = mcp.ssh_client.run("groups | grep -q '\\bsudo\\b'", io_timeout=5.0)
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
    use_sudo: Annotated[bool, Field(description="Use sudo for the kill operation")] = False,
    force: Annotated[bool, Field(description="Force kill with SIGKILL if process doesn't exit")] = True,
    wait_seconds: Annotated[float, Field(description="Seconds to wait before force kill", gt=0)] = 1.0
) -> dict:
    """
    Terminate a background task (launched via ssh_task_launch, or any other real
    remote PID) by sending a signal to its PID.

    If force=True and the process doesn't exit after wait_seconds,
    it will be forcibly killed with SIGKILL (signal 9).

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
        raise SshError("No active SSH connection")

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


@mcp.tool()
async def ssh_cmd_run(
        command: Annotated[str, Field(description="Command to execute on remote host")],
        io_timeout: Annotated[float, Field(description="Max seconds of SILENCE (no output) before giving up on waiting. Does NOT kill the remote command - hands off to background monitoring and returns control to you. Set this high (300+) for commands that may be quiet for a while: package installs (apt/dpkg/yum), Docker/image pulls, large downloads, compilation. If hit, call ssh_cmd_check_status or ssh_cmd_output to check back rather than rerunning.")] = 60.0,
        runtime_timeout: Annotated[Optional[float], Field(description="Total wall-clock cap in seconds, regardless of output activity. Unlike io_timeout/wait_timeout, hitting this DOES attempt to kill the remote command - it's the only hard ceiling. Set generously for long operations (installs/downloads can need 10-60+ minutes) - this should be a safety net, not a UX mechanism.", gt=0)] = None,
        use_sudo: Annotated[bool, Field(description="Run command with sudo")] = False,
        cwd: Annotated[Optional[str], Field(description="Run the command in this directory, for this call only (Linux/macOS only - not yet supported on Windows). Nothing is remembered between calls: each ssh_cmd_run is an independent process, so pass cwd again on every call where it matters, or chain 'cd dir && command' yourself. Fails closed - if the directory doesn't exist, the command is never executed at all (status='cwd_not_found'), so there's no ambiguity about where anything ran.")] = None,
        wait_timeout: Annotated[Optional[float], Field(description="Max seconds to wait in THIS call, regardless of output activity - unlike io_timeout, fires even if the command is actively producing output. Does NOT kill the remote command, same non-destructive handoff as io_timeout. Use this when you want to check in periodically on a command that's chatty but long-running (e.g. a verbose Docker pull), rather than being blocked until it finishes or goes quiet.", gt=0)] = None
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
    - For commands that should survive you disconnecting/reconnecting entirely (not just this
      call returning), use ssh_task_launch instead - it runs fully detached from this SSH session,
      whereas a command started here (even after surviving io_timeout/wait_timeout) is still tied
      to the current connection's lifetime.

    You can access command history using 'ssh_cmd_history' to see previous commands and their output.

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
        - 'busy': another ssh_cmd_run is already in flight on this connection - only
          one command can run at a time per connection.
        - 'error': unexpected failure (e.g. connection dropped).
    """
    if not mcp.ssh_client:
        return {
            'status': 'error',
            'error': "No active SSH connection",
            'command': command,
            'timestamp': datetime.now(UTC).isoformat()
        }

    try:
        handle = mcp.ssh_client.run(command, io_timeout, runtime_timeout, use_sudo, cwd=cwd, wait_timeout=wait_timeout)
        output = handle.get_full_output()
        stderr_output = handle.get_full_stderr()
        return {
            'status': 'success',
            'id': handle.id,
            'command': command,
            'exit_code': handle.exit_code,
            'output': output,
            'stderr': stderr_output,
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

        return {
            'status': e.reason,  # 'io_timeout' or 'wait_timeout'
            'id': handle.id if handle else None,
            'pid': handle.pid if handle else None,
            'command': command,
            'timeout_seconds': e.seconds,
            'output': handle.get_full_output() if handle else None,
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
    wait_seconds: Annotated[float, Field(description="Seconds to wait before checking", gt=0)] = 5.0
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
                    'output_lines': len(output) if output else 0
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
        lines: Annotated[Optional[int], Field(description="Number of most-recent lines to retrieve; None returns the last 50 (not necessarily the full output - see ssh_cmd_history for total/truncated line counts)")] = None,
        stream: Annotated[Literal['stdout', 'stderr'], Field(description="Which captured stream to retrieve - stdout (default) or stderr. These are NOT interleaved into one combined stream - call this twice (once per stream) if you need both")] = 'stdout'
) -> list:
    """
    Retrieve captured output from a command started with ssh_cmd_run, identified by
    its handle_id. Useful after an io_timeout/wait_timeout to see progress so far -
    including output produced after that call returned, since background monitoring
    keeps collecting it - or any time to re-inspect an earlier command's output
    without rerunning it.

    stdout and stderr are captured in separate buffers, not interleaved - `stream`
    picks which one to retrieve. ssh_cmd_run's own response only ever includes
    `output` (stdout); to see stderr from a successful command (warnings, progress
    meters, non-fatal messages - stderr on success is real output, not just for
    failures), call this tool with `stream='stderr'`.

    Raises an error if handle_id doesn't exist (e.g. from a previous connection -
    handles don't survive reconnects).

    Returns:
        A plain list of output lines from the selected stream (most recent `lines`,
        or the last 50 by default) - not a dict, no status/metadata. For total line
        counts and whether output was truncated, use ssh_cmd_history(include_output=True)
        instead (stdout only there).
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        return mcp.ssh_client.output(handle_id, lines=lines, stream=stream)
    except Exception as e:
        logger.error(f"Failed to retrieve output: {e}")
        raise


@mcp.tool()
async def ssh_cmd_clear_history() -> dict:
    """
    Clear the command history for the current SSH connection.
    
    Returns:
        Dictionary with operation status
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        cleared_count = mcp.ssh_client.history_manager.clear()

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
        pattern: Annotated[Optional[str], Field(description="Filter commands containing this pattern")] = None
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
        
        # Filter by pattern if specified
        if pattern is not None:
            history = [entry for entry in history if pattern in entry.get('cmd', '')]

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
        use_sudo: Annotated[bool, Field(description="Run command with sudo")] = False,
        stdout_log: Annotated[
            Optional[str], Field(description="Path to redirect stdout (default: /tmp/task-<pid>.log on Linux/macOS, C:\\Windows\\Temp\\task-<pid>.log on Windows)")] = None,
        stderr_log: Annotated[
            Optional[str], Field(description="Path to redirect stderr (default: same as stdout)")] = None,
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

    Returns:
        `{'command', 'pid', 'start_time', 'stdout_log', 'stderr_log'}`. `stdout_log`/
        `stderr_log` are `None` if `log_output=False`. If you didn't pass an explicit
        `stdout_log`/`stderr_log` yourself, the returned path reflects the actual
        default log location used - `/tmp/task-<pid>.log` on Linux/macOS,
        `C:\\Windows\\Temp\\task-<pid>.log` on Windows.
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        # Don't add tasks to command history
        handle = mcp.ssh_client.launch(command, use_sudo, stdout_log, stderr_log, log_output, add_to_history=False)
        default_log_dir = mcp.ssh_client.task_ops._get_default_log_dir()
        return {
            'command': command,
            'pid': handle.pid,
            'start_time': handle.start_ts.isoformat() if handle.start_ts else None,
            'stdout_log': (stdout_log or f"{default_log_dir}/task-{handle.pid}.log") if log_output else None,
            'stderr_log': (stderr_log or f"{default_log_dir}/task-{handle.pid}.log") if log_output else None
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
        raise SshError("No active SSH connection")
        
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
        raise SshError("No active SSH connection")
        
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
        raise SshError("No active SSH connection")

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
        raise SshError("No active SSH connection")

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
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.find_lines_with_pattern(file_path, pattern, regex, use_sudo)
    except Exception as e:
        logger.error(f"Failed to search file: {e}")
        raise

@mcp.tool()
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
        raise SshError("No active SSH connection")
        
    try:
        return mcp.ssh_client.get_context_around_line(file_path, match_line, context, use_sudo)
    except Exception as e:
        logger.error(f"Failed to get context: {e}")
        raise

@mcp.tool()
async def ssh_file_replace_line(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match and replace")],
    new_line: Annotated[str, Field(description="New line to insert in place of the match")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="Force operation even if file can't be read (sudo only)")] = False
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
    * force: Force operation even if file can't be read (sudo only) (default: false)

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
        raise SshError("No active SSH connection")
        
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
async def ssh_file_replace_line_multi(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match and replace")],
    new_lines: Annotated[list, Field(description="List of new lines to insert in place of the match")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="Force operation even if file can't be read (sudo only)")] = False
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
    * force: Force operation even if file can't be read (sudo only) (default: false)

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
        raise SshError("No active SSH connection")
        
    try:
        # Use the Pydantic model to parse and validate the new_lines parameter
        parsed_new_lines = NewLinesModel.parse(new_lines)
        logger.info(f"Processed new_lines parameter: {parsed_new_lines}")

        result = mcp.ssh_client.replace_line_by_content(file_path, match_line, parsed_new_lines, use_sudo, force)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to replace line: {e}")
        raise


@mcp.tool()
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
        raise SshError("No active SSH connection")

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
                mcp.ssh_client.put(local_path, temp_remote_path)
                
                # Then move it to the final location with sudo
                move_cmd = f"mv {shlex.quote(temp_remote_path)} {shlex.quote(remote_path)}"
                mcp.ssh_client.run(move_cmd, sudo=True)
                operation = f"Uploaded {local_path} to {remote_path} with sudo"
            else:
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
                mcp.ssh_client.get(temp_remote_path, local_path)
                
                # Clean up
                rm_cmd = f"rm -f {shlex.quote(temp_remote_path)}"
                mcp.ssh_client.run(rm_cmd, sudo=True)
                
                operation = f"Downloaded {remote_path} to {local_path} with sudo"
            else:
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

    Args:
        direction: 'upload' (local to remote) or 'download' (remote to local)
        local_path: Local directory path
        remote_path: Remote directory path
        use_sudo: Use sudo for remote archive/extract operations (Linux/macOS only)

    Returns:
        Dictionary containing transfer status and metadata:
        - success: Boolean indicating if transfer succeeded
        - operation: 'upload' or 'download'
        - local_path: Local directory path
        - remote_path: Remote directory path
        - archive_format: 'tar.gz' or 'zip'
        - files_transferred: Number of files transferred
        - bytes_transferred: Total bytes transferred (archive size)
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

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
async def ssh_file_insert_lines_after_match(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match")],
    lines_to_insert: Annotated[list, Field(description="Line(s) to insert after the match")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="Force operation even if file can't be read (sudo only)")] = False
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
    * force: Force operation even if file can't be read (sudo only) (default: false)

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
        raise SshError("No active SSH connection")
        
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
async def ssh_file_delete_line_by_content(
    file_path: Annotated[str, Field(description="Path to the file to modify")],
    match_line: Annotated[str, Field(description="Exact line content to match and delete")],
    use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False,
    force: Annotated[bool, Field(description="Force operation even if file can't be read (sudo only)")] = False
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
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.delete_line_by_content(file_path, match_line, use_sudo, force)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to delete line: {e}")
        raise

@mcp.tool()
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
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.copy_file(source_path, destination_path, append_timestamp, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to copy file: {e}")
        raise


@mcp.tool()
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
        raise SshError("No active SSH connection")
        
    try:
        # Create a local temporary file with the content
        # Ensure we use Unix-style line endings (LF) for consistency
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, newline='\n', encoding='utf-8') as temp_file:
            temp_file.write(content)
            local_temp_path = temp_file.name
        
        try:
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
                            mcp.ssh_client.run(mkdir_cmd, sudo=True)
                        else:
                            mcp.ssh_client.run(mkdir_cmd)
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
                        mcp.ssh_client.run(powershell_encoded_command(copy_cmd))
                        # Clean up temp file
                        mcp.ssh_client.run(powershell_encoded_command(f"Remove-Item -Path '{remote_temp_path}' -Force -ErrorAction SilentlyContinue"))
                    else:
                        if not append:
                            # For overwrite with sudo, use cat with sudo redirection
                            cat_cmd = f"cat {shlex.quote(remote_temp_path)} > {shlex.quote(file_path)}"
                            mcp.ssh_client.run(f"sh -c {shlex.quote(cat_cmd)}", sudo=True)
                        else:
                            # For append with sudo, use cat with sudo append redirection
                            cat_cmd = f"cat {shlex.quote(remote_temp_path)} >> {shlex.quote(file_path)}"
                            mcp.ssh_client.run(f"sh -c {shlex.quote(cat_cmd)}", sudo=True)
                        # Clean up the temporary file
                        mcp.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}")
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
                                mcp.ssh_client.run(powershell_encoded_command(copy_cmd))
                                mcp.ssh_client.run(powershell_encoded_command(f"Remove-Item -Path '{remote_temp_path}' -Force -ErrorAction SilentlyContinue"))
                            else:
                                remote_temp_path = f"/tmp/ssh_file_write_{os.path.basename(file_path)}_{int(time.time())}"
                                mcp.ssh_client.put(local_temp_path, remote_temp_path)
                                cat_cmd = f"cat {shlex.quote(remote_temp_path)} > {shlex.quote(file_path)}"
                                mcp.ssh_client.run(f"sh -c {shlex.quote(cat_cmd)}", sudo=True)
                                mcp.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}")
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
                            mcp.ssh_client.run(mkdir_cmd, sudo=True)
                        else:
                            mcp.ssh_client.run(mkdir_cmd)
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
                mcp.ssh_client.run(chmod_cmd, sudo=use_sudo)
                
            # If sudo was used, we may need to check ownership
            if use_sudo:
                # Get the current user to set ownership properly
                whoami_result = mcp.ssh_client.run("whoami")
                current_user = whoami_result.get_full_output().strip()
                if current_user and current_user != "root":
                    # Set ownership to the current user if we're not root
                    chown_cmd = f"chown {current_user} {shlex.quote(file_path)}"
                    try:
                        mcp.ssh_client.run(chown_cmd, sudo=True)
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
        raise SshError("No active SSH connection")

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

    Returns:
        List of `{'path': str, 'type': str}` - `type` is a single-character code
        (`f`=file, `d`=directory, `l`=symlink) from the underlying `find`/`stat`
        output, not a spelled-out word.
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        # Check the signature of search_files_recursive and pass only the arguments it accepts
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
    Calculate the total size of a directory recursively (sum of all file sizes
    under it).

    Returns:
        `{'path', 'size_bytes' (int), 'size_human' (e.g. "1.23 MB", "512.00 KB",
        "3.50 GB" - 2 decimal places, binary/1024-based units)}`.
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
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.delete_directory_recursive(path, dry_run, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to delete directory: {e}")
        raise


@mcp.tool()
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

    Returns:
        `{'status': 'success'/'error', 'deleted_files': [str, ...] (matching file
        paths that were/would be deleted)}`, plus `'dry_run': True` ONLY when this
        was a preview (check for this key to tell a preview apart from a real
        deletion) and `'error'` (string) on failure. Note the key is `deleted_files`
        here, vs. `deleted_items` on ssh_dir_delete.
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.batch_delete_by_pattern(path, pattern, dry_run, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to batch delete files: {e}")
        raise


@mcp.tool()
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
        raise SshError("No active SSH connection")
        
    try:
        results = mcp.ssh_client.list_directory_recursive(path, max_depth, use_sudo)
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
        use_sudo: Annotated[bool, Field(description="Use sudo for the operation")] = False
) -> list:
    """
    Recursively search file CONTENTS for a pattern under a directory (unlike
    ssh_dir_search_glob/ssh_file_find_lines_with_pattern, which match filenames or
    search within a single already-known file respectively).

    Regex flavor differs by platform when `regex=True`: POSIX extended regex
    (`grep -E`) on Linux/macOS - avoid PCRE-only syntax like `\\d`, use `[0-9]` or
    `[[:digit:]]` instead; Python's `re` module on Windows (matched locally after
    an SFTP read per file, not via PowerShell - see below). When `regex=False`
    (default), the pattern is matched as a literal fixed string.

    On Windows, filenames are enumerated via PowerShell but each file's content is
    read via SFTP and matched locally in Python, rather than piping matched line
    content back through PowerShell/Select-String - that content could otherwise
    come back corrupted for non-ASCII text, since Windows' console encodes stdout
    in its OEM code page rather than UTF-8 (the same problem ssh_file_read's SFTP
    approach avoids).

    Returns:
        List of `{'file': str, 'line': int, 'content': str}` - one entry per matching
        line, across all files under `dir_path`. Empty list if nothing matches (not
        an error).
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")

    try:
        results = mcp.ssh_client.search_file_contents(dir_path, pattern, regex, case_sensitive, use_sudo)
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
        raise SshError("No active SSH connection")

    try:
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
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.create_archive_from_directory(source_path, archive_path, format, use_sudo)
        result['connection'] = _connection_metadata()
        return result
    except Exception as e:
        logger.error(f"Failed to create archive: {e}")
        raise


@mcp.tool()
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

    Returns:
        On success: `{'status': 'success', 'success': True, 'extracted_files': [str,
        ...] (paths as listed inside the archive), 'destination_path'}`. On failure
        (wrong format for this platform, extraction error): `{'status': 'error',
        'message': str, 'extracted_files': []}` - not a raised exception.
    """
    if not mcp.ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        result = mcp.ssh_client.extract_archive_to_directory(archive_path, destination_path, overwrite, use_sudo)
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
    global host_manager, _default_host_manager

    # Parse command line arguments
    args = parse_args()

    # Re-initialize host manager with config path if provided
    host_manager = SshHostManager(
        config_path=Path(args.config) if args.config else None
    )
    _default_host_manager = host_manager

    try:
        logger.info(f"Starting SSH MCP server '{mcp.name}' ")
        logger.info(f"Using TOML config file: {host_manager.config_path}")
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
