import logging
import sys
from fastmcp import FastMCP
from pydantic import Field
from typing import Annotated
from ssh_client import SshClient

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
    logger.info(f"Created MCP server instance '{mcp.name}'")
except Exception as e:
    logger.critical(f"Failed to create MCP instance: {e}", exc_info=True)
    sys.exit(1)

# ===================
# Global State
# ===================

# Global SSH client instance
ssh_client = None

# ===================
# Cleanup Handlers
# ===================

@mcp.on_shutdown
async def cleanup_ssh():
    """Clean up SSH connection when server shuts down."""
    global ssh_client
    if ssh_client:
        logger.info("Closing SSH connection on shutdown")
        try:
            ssh_client.close()
        except Exception as e:
            logger.error(f"Error closing SSH connection: {e}")
        finally:
            ssh_client = None
    logger.info("SSH cleanup complete")

# ===================
# Core SSH Tools
# ===================

@mcp.tool()
async def ssh_connect(
    host: Annotated[str, Field(description="Remote hostname or IP address")],
    user: Annotated[str, Field(description="Username for authentication")],
    password: Annotated[Optional[str], Field(description="Password for authentication", secret=True)] = None,
    port: Annotated[int, Field(description="SSH port", ge=1, le=65535)] = 22,
    keyfile: Annotated[Optional[str], Field(description="Path to SSH private key file")] = None
) -> dict:
    """
    Establish an SSH connection to a remote host.
    
    Returns:
        Dictionary with connection status and metadata
    """
    global ssh_client
    try:
        if ssh_client:
            logger.warning("Closing existing SSH connection")
            ssh_client.close()
            
        ssh_client = SshClient(
            host=host,
            user=user,
            password=password,
            port=port,
            keyfile=keyfile
        )
        status = ssh_client.get_connection_status()
        logger.info(f"Successfully connected to {user}@{host}:{port}")
        return status
    except Exception as e:
        logger.error(f"Failed to connect to {host}: {e}")
        raise

@mcp.tool()
async def ssh_run(
    command: Annotated[str, Field(description="Command to execute on remote host")],
    io_timeout: Annotated[float, Field(description="I/O timeout in seconds", gt=0)] = 60.0,
    runtime_timeout: Annotated[Optional[float], Field(description="Total runtime timeout in seconds", gt=0)] = None,
    sudo: Annotated[bool, Field(description="Run command with sudo")] = False
) -> dict:
    """
    Execute a command on the remote host and return the results.
    
    Returns:
        Dictionary containing command output and metadata
    """
    if not ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        handle = ssh_client.run(command, io_timeout, runtime_timeout, sudo)
        output = handle.get_full_output()
        return {
            'command': command,
            'exit_code': handle.exit_code,
            'output': output,
            'pid': handle.pid,
            'start_time': handle.start_ts.isoformat(),
            'end_time': handle.end_ts.isoformat() if handle.end_ts else None
        }
    except Exception as e:
        logger.error(f"Command execution failed: {e}")
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
    if not ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        if direction == 'upload':
            ssh_client.put(local_path, remote_path, sudo)
            operation = f"Uploaded {local_path} to {remote_path}"
        else:
            ssh_client.get(remote_path, local_path, sudo)
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

@mcp.tool()
async def ssh_status() -> dict:
    """
    Get current SSH connection status and system information.
    
    Returns:
        Dictionary containing connection status and system info
    """
    if not ssh_client:
        raise SshError("No active SSH connection")
        
    try:
        status = ssh_client.get_connection_status()
        system_info = ssh_client.full_status()
        return {
            'connection': status,
            'system': system_info
        }
    except Exception as e:
        logger.error(f"Failed to get status: {e}")
        raise

# ===================
# Main Execution
# ===================

if __name__ == '__main__':
    try:
        logger.info(f"Starting SSH MCP server '{mcp.name}' version {mcp.version}")
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
