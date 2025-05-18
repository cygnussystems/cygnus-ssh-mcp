
import pytest
import asyncio
import sys
import os
import logging
import json
import subprocess
import time
from pathlib import Path

# Import necessary modules
from mcp_ssh_server import mcp, host_manager # Import host_manager for potential cleanup
from fastmcp import Client
from ssh_client import SshClient
from ssh_host_manager import SshHostManager


# SSH test container management
async def docker_test_environment(user: str, password: str, host: str = "localhost", base_port: int = 2222):
    """
    Set up the test environment by starting an SSH server container.
    Also ensures the test TOML config file is clean for SshHostManager.
    
    Args:
        user: SSH username for the test container
        password: SSH password for the test container
        host: Hostname to use (usually localhost)
        base_port: Starting port to try for SSH (will increment if busy)
        
    Returns:
        The actual port being used for SSH
    """
    # Import the global variable to modify it
    global SSH_TEST_PORT
    
    # Set the initial port value from the parameter
    SSH_TEST_PORT = base_port
    logger = logging.getLogger("test_setup")
    logger.info("Setting up test environment")

    # Clean up any existing test TOML config file to ensure a fresh start for SshHostManager
    # This is important because SshHostManager might load an existing file from a previous run.
    test_config_path_project = Path("ssh_hosts.toml")
    test_config_path_home = Path.home() / ".ssh_hosts.toml"
    if test_config_path_project.exists():
        logger.info(f"Removing existing test config: {test_config_path_project}")
        test_config_path_project.unlink()
    if test_config_path_home.exists() and host_manager.config_path == test_config_path_home :
        # Only remove home if it's the one SshHostManager would actually use by default
        logger.info(f"Removing existing test config: {test_config_path_home}")
        test_config_path_home.unlink()
    # Re-initialize host_manager to ensure it creates/loads a fresh config
    # This assumes host_manager is the global instance from mcp_ssh_server
    # The current structure initializes host_manager at import time of mcp_ssh_server.
    # To ensure a fresh state for tests, we can re-initialize it here if needed,
    # or rely on the fact that if its default config file is removed, it will create a new one.
    # For robustness, explicitly re-instantiate or tell SshHostManager to reload.
    # For now, we'll assume file removal + SshHostManager's own _ensure_config_file is enough.
    # If mcp_ssh_server.host_manager is used globally by tests, it might need explicit reloading.
    # The most robust way is for SshHostManager to be instantiated by the test session or for
    # host_manager.config_path to be set to a temporary test-specific file.
    # For now, we rely on the default behavior after cleaning up potential default files.

    # Check if the ssh-test container is already running
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=ssh-test-server", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False # Don't check=True, handle empty output
        )

        if "ssh-test-server" in result.stdout:
            logger.info("SSH test container 'ssh-test-server' is already running")
            port_result = subprocess.run(
                ["docker", "port", "ssh-test-server", "22"],
                capture_output=True,
                text=True,
                check=True
            )
            if port_result.stdout.strip():
                port_mapping = port_result.stdout.strip()
                if ":" in port_mapping:
                    SSH_TEST_PORT = int(port_mapping.split(":")[-1])
                    # Update SSH_TEST_CONNECTION_PARAMS if port changed
                    logger.info(f"Using existing container with port {SSH_TEST_PORT}")
            return
    except subprocess.CalledProcessError as e:
        logger.warning(f"Error checking for existing container: {e}")
    except FileNotFoundError:
        logger.error("Docker command not found. Please ensure Docker is installed and in PATH.")
        raise

    # We've already removed the container above, so we don't need to do it again

    # Check if the container already exists and remove it
    try:
        subprocess.run(["docker", "rm", "-f", "ssh-test-server"], check=False, capture_output=True)
        logger.info("Removed existing ssh-test-server container if it existed")
    except Exception as e:
        logger.warning(f"Error removing existing container: {e}")

    # Find an available port
    import socket
    original_port = SSH_TEST_PORT
    max_port_attempts = 10

    for attempt in range(max_port_attempts):
        # First check if Docker has the port allocated
        try:
            port_check = subprocess.run(
                ["docker", "ps", "-a", "--format", "{{.Ports}}"], 
                capture_output=True, 
                text=True, 
                check=True
            )
            if f":{SSH_TEST_PORT}->" in port_check.stdout or f":{SSH_TEST_PORT}/" in port_check.stdout:
                logger.warning(f"Port {SSH_TEST_PORT} is already allocated in Docker, trying next port")
                SSH_TEST_PORT += 1
                continue
        except Exception as e:
            logger.warning(f"Error checking Docker ports: {e}")
            
        # Then check if the port is available on the host
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', SSH_TEST_PORT))
            s.close()
            logger.info(f"Port {SSH_TEST_PORT} is available")
            break
        except socket.error:
            s.close()
            logger.warning(f"Port {SSH_TEST_PORT} is not available, trying next port {SSH_TEST_PORT + 1}")
            SSH_TEST_PORT += 1
            if attempt == max_port_attempts - 1:
                raise RuntimeError \
                    (f"Could not find an available port after {max_port_attempts} attempts, starting from {original_port}")

    if SSH_TEST_PORT != original_port:
        logger.info(f"Using port {SSH_TEST_PORT} instead of {original_port}")

    # Start the SSH test container
    try:
        logger.info(f"Starting SSH test container 'ssh-test-server' on port {SSH_TEST_PORT}")
        docker_run_cmd = [
            "docker", "run", "-d",
            "--name", "ssh-test-server",
            "-p", f"{SSH_TEST_PORT}:22",
            "-e", f"USER_NAME={user}",
            "-e", f"USER_PASSWORD={password}",
            "-e", "SUDO_ACCESS=true",
            "-e", "PASSWORD_ACCESS=true",
            "linuxserver/openssh-server:latest"
        ]
        subprocess.run(docker_run_cmd, check=True)

        logger.info("Waiting for SSH server to be ready (approx. 15-20s)")
        time.sleep(15) # Increased wait time for container stability on Windows
            
        # Check if the container is actually running and ready
        try:
            # First check if container is running
            container_check = subprocess.run(
                ["docker", "ps", "--filter", "name=ssh-test-server", "--format", "{{.Status}}"],
                capture_output=True, text=True, check=False
            )
            if not container_check.stdout.strip():
                logger.error("Container is not running after initial wait!")
                exit_check = subprocess.run(
                    ["docker", "ps", "-a", "--filter", "name=ssh-test-server", "--format", "{{.Status}}"],
                    capture_output=True, text=True, check=False
                )
                if exit_check.stdout.strip():
                    logger.error(f"Container exited: {exit_check.stdout.strip()}")
                    
                # Get logs to diagnose why it's not running
                logs_result = subprocess.run(["docker", "logs", "ssh-test-server"], 
                                           capture_output=True, text=True, check=False)
                if logs_result.stdout:
                    logger.error(f"Container logs (stdout):\n{logs_result.stdout}")
                if logs_result.stderr:
                    logger.error(f"Container logs (stderr):\n{logs_result.stderr}")
            else:
                logger.info(f"Container is running: {container_check.stdout.strip()}")
                    
            # Try a simple TCP connection to port 22 in the container to check if SSH is listening
            logger.info(f"Testing TCP connection to port 22 in container...")
            tcp_check = subprocess.run(
                ["docker", "exec", "ssh-test-server", "nc", "-z", "-v", "localhost", "22"],
                capture_output=True, text=True, check=False
            )
            if tcp_check.returncode == 0:
                logger.info("SSH port is listening inside container")
            else:
                logger.warning(f"SSH port check inside container failed: {tcp_check.stderr}")
        except Exception as e:
            logger.warning(f"Error checking container readiness: {e}")

        max_retries = 8
        retry_delay = 2 # Start with a shorter delay but increase it exponentially

        for attempt_conn in range(max_retries):
            try:
                # Use SshClient directly for initial check, not MCP tools yet
                # Add a longer connection timeout for Windows environments
                temp_client = SshClient(
                    host=host,
                    user=user,
                    port=SSH_TEST_PORT,
                    password=password,
                    timeout=10.0  # Increase connection timeout
                )
                result = temp_client.run("echo 'SSH connection test successful'")
                temp_client.close()
                if result.exit_code == 0:
                    logger.info("SSH test environment is ready.")
                    return
                else:
                    logger.warning(f"SSH connection test command failed with exit code {result.exit_code}.")
            except Exception as e:
                logger.warning(f"SSH connection attempt {attempt_conn + 1}/{max_retries} to container failed: {e}")

            if attempt_conn < max_retries - 1:
                # Use exponential backoff with a small random component
                current_delay = retry_delay * (1.5 ** attempt_conn)
                logger.info(f"Waiting {current_delay:.1f}s before next connection attempt...")
                time.sleep(current_delay)
            else:
                # Check if container is actually running
                try:
                    container_check = subprocess.run(
                        ["docker", "ps", "--filter", "name=ssh-test-server", "--format", "{{.Status}}"],
                        capture_output=True, text=True, check=False
                    )
                    if container_check.stdout.strip():
                        logger.info(f"Container status: {container_check.stdout.strip()}")
                    else:
                        logger.error("Container is not running! Checking for exit status...")
                        exit_check = subprocess.run(
                            ["docker", "ps", "-a", "--filter", "name=ssh-test-server", "--format", "{{.Status}}"],
                            capture_output=True, text=True, check=False
                        )
                        if exit_check.stdout.strip():
                            logger.error(f"Container exited: {exit_check.stdout.strip()}")
                except Exception as e:
                    logger.error(f"Error checking container status: {e}")
                    
                # Attempt to get container logs if connection fails
                try:
                    logs_result = subprocess.run(["docker", "logs", "ssh-test-server"], capture_output=True, text=True, check=False)
                    if logs_result.stdout:
                        logger.error(f"SSH test server container logs (stdout):\n{logs_result.stdout}")
                    if logs_result.stderr:
                        logger.error(f"SSH test server container logs (stderr):\n{logs_result.stderr}")
                except Exception as log_e:
                    logger.error(f"Could not retrieve container logs: {log_e}")
                    
                # On Windows, check if Windows Defender or other security software might be blocking
                if sys.platform == 'win32':
                    logger.error("On Windows, this error often occurs due to Windows Defender or other security software.")
                    logger.error("Consider temporarily disabling firewall or adding an exception for Docker/SSH.")
                    
                raise RuntimeError(f"Failed to connect to SSH test server in container after {max_retries} attempts.")

    except subprocess.CalledProcessError as e:
        logger.error \
            (f"Failed to start Docker container: {e}. Command: {' '.join(e.cmd)}. Output: {e.output}. Stderr: {e.stderr}")
        raise
    except FileNotFoundError:
        logger.error("Docker command not found. Please ensure Docker is installed and in PATH.")
        raise
    except Exception as e:
        logger.error(f"Failed to set up test environment: {e}")
        raise



async def teardown_test_environment():
    """
    Clean up the test environment by stopping and removing the SSH server container.
    """
    logger = logging.getLogger("test_teardown")
    logger.info("Tearing down test environment")

    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=ssh-test-server", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False # Don't fail if no container
        )

        if "ssh-test-server" in result.stdout:
            logger.info("Stopping SSH test container 'ssh-test-server'")
            subprocess.run(["docker", "stop", "ssh-test-server"], check=False, capture_output=True)
            logger.info("Removing SSH test container 'ssh-test-server'")
            subprocess.run(["docker", "rm", "ssh-test-server"], check=False, capture_output=True)
            logger.info("SSH test container 'ssh-test-server' stopped and removed.")
        else:
            logger.info("SSH test container 'ssh-test-server' not found, no cleanup needed.")
    except FileNotFoundError:
        logger.warning("Docker command not found. Cannot stop/remove container. Manual cleanup might be needed.")
    except Exception as e:
        logger.error(f"Error during test environment teardown: {e}")
