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
# Main Execution
# ===================

if __name__ == '__main__':
    try:
        logger.info(f"Starting SSH MCP server '{mcp.name}' version {mcp.version}")
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by user (KeyboardInterrupt)")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Server crashed with error: {e}", exc_info=True)
        sys.exit(1)
