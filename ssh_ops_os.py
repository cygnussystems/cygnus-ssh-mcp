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
    
    def hardware_info(self):
        """
        Get hardware-related information (CPU, memory, etc.).
        
        Returns:
            Dict containing hardware information.
        """
        cmd = r"""
        bash -c '
          echo "CPU:$(grep -c ^processor /proc/cpuinfo)"
          echo "MEM_TOTAL:$(free -m | awk "/^Mem:/{print $2}")"
          echo "MEM_FREE:$(free -m | awk "/^Mem:/{print $4}")"
          echo "LOAD:$(cut -d" " -f1-3 /proc/loadavg 2>/dev/null || echo n/a)"
        '
        """
        return self._execute_status_command(cmd, {
            'CPU': 'cpu_count',
            'MEM_TOTAL': 'mem_total_mb',
            'MEM_FREE': 'mem_free_mb',
            'LOAD': 'load_avg'
        })
    
    def network_info(self):
        """
        Get network-related information (interfaces, IPs, etc.).
        
        Returns:
            Dict containing network information.
        """
        cmd = r"""
        bash -c '
          echo "HOSTNAME:$(hostname)"
          echo "IP:$(hostname -I | awk "{print $1}" 2>/dev/null || echo n/a)"
        '
        """
        return self._execute_status_command(cmd, {
            'HOSTNAME': 'hostname',
            'IP': 'ip_address'
        })
    
    def disk_info(self):
        """
        Get disk-related information (usage, free space, etc.).
        
        Returns:
            Dict containing disk information.
        """
        cmd = r"""
        bash -c '
          echo "DISK_TOTAL:$(df -h / | awk "NR==2{print $2}")"
          echo "DISK_FREE:$(df -h / | awk "NR==2{print $4}")"
        '
        """
        return self._execute_status_command(cmd, {
            'DISK_TOTAL': 'disk_total',
            'DISK_FREE': 'disk_free'
        })
    
    def user_status(self):
        """
        Get user-related information (username, working directory, time).
        
        Returns:
            Dict containing user information.
        """
        cmd = r"""
        bash -c '
          echo "USER:$(whoami)"
          echo "CWD:$(pwd)"
          echo "TIME:$(date -Is)"
        '
        """
        return self._execute_status_command(cmd, {
            'USER': 'user',
            'CWD': 'cwd',
            'TIME': 'time'
        })
    
    def status(self):
        """
        Return a combined snapshot of system state.
        
        Returns:
            Dict containing all system status information.
        """
        status_info = {}
        status_info.update(self.user_status())
        status_info.update(self.hardware_info())
        status_info.update(self.network_info())
        status_info.update(self.disk_info())
        return status_info
    
    def _execute_status_command(self, cmd, key_map):
        """
        Execute a status command and parse its output.
        
        Args:
            cmd: The command to execute.
            key_map: Mapping of output keys to result keys.
            
        Returns:
            Dict containing parsed status information.
        """
        status_info = {}
        try:
            handle = self.ssh_client.run(cmd.strip(), io_timeout=5, runtime_timeout=10)
            output = "".join(handle.tail(20)) # Get all lines
            for line in output.splitlines():
                if ':' in line:
                    key, value = line.split(':', 1) # Split only on the first colon
                    status_info[key_map.get(key.strip(), key.strip().lower())] = value.strip()
        except BusyError:
            self.logger.warning("Cannot get status: client is busy.")
            raise
        except Exception as e:
            self.logger.warning(f"Failed to get status: {e}", exc_info=True)
            return {'error': str(e)}
        
        # Ensure all expected keys are present, even if 'n/a'
        for key in key_map.values():
            if key not in status_info:
                status_info[key] = 'n/a'
        
        return status_info
