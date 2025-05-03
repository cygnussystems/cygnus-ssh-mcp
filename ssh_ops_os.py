import time
import logging
from ssh_models import SshError, CommandTimeout, BusyError

class SshOsOperations:
    """Handles operating system level operations like reboot and status."""
    
    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshOsOperations")
    
    def reboot(self, wait=True, timeout=300):
        """
        Reboot the remote host and optionally wait until it comes back.
        
        Args:
            wait: Whether to wait for the host to come back online
            timeout: Maximum time to wait in seconds
            
        Returns:
            None on success
            
        Raises:
            SshError: If reboot fails
            CommandTimeout: If wait=True and host doesn't come back within timeout
        """
        self.logger.warning("Attempting reboot...")
        try:
            # Use run_ops to execute the reboot command
            self.ssh_client.run_ops.execute_command('reboot', sudo=True, runtime_timeout=10)
            self.logger.info("Reboot command executed successfully (connection likely dropping).")
        except Exception as e:
            self.logger.error(f"Reboot failed: {e}")
            raise SshError(f"Reboot failed: {e}") from e
        finally:
            self.ssh_client.close()

        if wait:
            self.logger.info(f"Waiting up to {timeout} seconds for host {self.ssh_client.host} to come back online...")
            start = time.time()
            while time.time() - start < timeout:
                try:
                    self.ssh_client._connect()  # Try to reconnect
                    self.logger.info("Reconnected successfully after reboot.")
                    return
                except Exception:
                    time.sleep(5)
            raise CommandTimeout(timeout)
    
    def status(self):
        """
        Return a snapshot of system state using a combined command.
        
        Returns:
            Dict containing system status information
        """
        # Combined command for efficiency - use raw string literal
        cmd = r"""
        bash -c '
          echo "USER:$(whoami)"
          echo "CWD:$(pwd)"
          echo "TIME:$(date -Is)"
          echo "HOST:$(hostname)"
          echo "UP:$(uptime -p 2>/dev/null || uptime)"
          echo "LOAD:$(cut -d" " -f1-3 /proc/loadavg 2>/dev/null || echo n/a)"
          echo "DISK:$(df -h / 2>/dev/null | awk "NR==2{print $4}" || echo n/a)"
          echo "MEM:$(free -m 2>/dev/null | awk "/^Mem:/{print $4\" MB\"}" || echo n/a)"
          if [ -f /etc/os-release ]; then . /etc/os-release; echo "OS:${NAME} ${VERSION_ID}"; else uname -srm; fi
        '
        """
        # Note: $4 in awk commands no longer needs escaping due to raw string
        # Escaped quote for " MB" still needed: \"
        status_info = {}
        try:
            # Use run with short timeouts
            handle = self.ssh_client.run(cmd.strip(), io_timeout=5, runtime_timeout=10)
            output = "".join(handle.tail(20)) # Get all lines
            for line in output.splitlines():
                if ':' in line:
                    key, value = line.split(':', 1) # Split only on the first colon
                    key_map = {
                        'USER': 'user', 'CWD': 'cwd', 'TIME': 'time', 'HOST': 'host',
                        'UP': 'uptime', 'LOAD': 'load_avg', 'DISK': 'free_disk',
                        'MEM': 'mem_free', 'OS': 'os'
                    }
                    status_info[key_map.get(key.strip(), key.strip().lower())] = value.strip()
        except BusyError:
            self.logger.warning("Cannot get status: client is busy.")
            raise # Propagate busy error
        except Exception as e:
            self.logger.warning(f"Failed to get full status: {e}", exc_info=True)
            return {'error': str(e)} # Return error dict

        # Ensure all expected keys are present, even if 'n/a'
        expected_keys = ['user', 'cwd', 'time', 'os', 'host', 'uptime', 'load_avg', 'free_disk', 'mem_free']
        for key in expected_keys:
            if key not in status_info:
                status_info[key] = 'n/a'

        return status_info
