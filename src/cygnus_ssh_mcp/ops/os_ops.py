import time
import logging
from typing import Dict, Any
from cygnus_ssh_mcp.models import SshError, CommandTimeout, BusyError, CommandFailed

class SshOsOperations_Win:
    """Handles OS-level operations on Windows systems."""
    
    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshOsOperations_Win")

class SshOsOperations_Linux:
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
            # Use a short I/O timeout as we don't expect much output before disconnect
            self.ssh_client.run_ops.execute_command('reboot', sudo=True, io_timeout=5, runtime_timeout=10)
            self.logger.info("Reboot command executed successfully (connection likely dropping).")
        except CommandTimeout:
             # A timeout here might be expected if the connection drops before command ack
             self.logger.info("Reboot command sent, connection likely dropped as expected.")
        except Exception as e:
            # Log other unexpected errors but proceed assuming reboot might have initiated
            self.logger.error(f"Error sending reboot command: {e}")
            # Don't re-raise immediately if wait=True, give it a chance to come back
            if not wait:
                 raise SshError(f"Reboot failed: {e}") from e
        finally:
            # Ensure connection is closed before waiting loop
            self.ssh_client.close()

        if wait:
            self.logger.info(f"Waiting up to {timeout} seconds for host {self.ssh_client.host} to come back online...")
            start = time.time()
            reconnected = False
            while time.time() - start < timeout:
                try:
                    time.sleep(5) # Wait before attempting reconnect
                    self.ssh_client._connect()  # Try to reconnect
                    self.logger.info("Reconnected successfully after reboot.")
                    reconnected = True
                    return # Success
                except Exception as connect_err:
                    self.logger.debug(f"Reconnect attempt failed: {connect_err}")
                    # Continue waiting
            
            # If loop finishes without reconnecting
            if not reconnected:
                self.logger.error(f"Host did not come back online within {timeout} seconds.")
                raise CommandTimeout(timeout) # Raise timeout error specifically
    
    def hardware_info(self):
        """
        Get comprehensive hardware-related information.
        
        Returns:
            Dict containing:
            - cpu_count: Number of CPU cores
            - cpu_model: CPU model name
            - cpu_mhz: CPU frequency
            - mem_total_mb: Total memory in MB
            - mem_free_mb: Free memory in MB
            - mem_available_mb: Available memory in MB
            - load_avg: 1, 5, and 15 minute load averages
        """
        cmd = r"""
        bash -c '
          echo "CPU:$(grep -c ^processor /proc/cpuinfo)"
          echo "CPU_MODEL:$(grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | sed "s/^[ \t]*//;s/[ \t]*$//")"
          echo "CPU_MHZ:$(grep -m1 "cpu MHz" /proc/cpuinfo | cut -d: -f2 | sed "s/^[ \t]*//;s/[ \t]*$//")"
          echo "MEM_TOTAL:$(free -m | awk "/^Mem:/{print \$2}")"
          echo "MEM_FREE:$(free -m | awk "/^Mem:/{print \$4}")"
          echo "MEM_AVAIL:$(free -m | awk "/^Mem:/{print \$7}")"
          echo "LOAD:$(cut -d" " -f1-3 /proc/loadavg 2>/dev/null || echo n/a)"
        '
        """
        return self._execute_status_command(cmd, self._hardware_key_map)

    def os_info(self):
        """
        Get operating system information.
        
        Returns:
            Dict containing:
            - os_name: OS name (e.g. "Ubuntu")
            - os_version: OS version
            - os_release: OS release (e.g. "20.04")
            - kernel: Kernel version
            - architecture: System architecture
        """
        cmd = r"""
        bash -c '
          if [ -f /etc/os-release ]; then
            echo "OS_NAME:$(grep "^NAME=" /etc/os-release | cut -d= -f2 | tr -d \")"
            echo "OS_VERSION:$(grep "^VERSION=" /etc/os-release | cut -d= -f2 | tr -d \")"
            echo "OS_RELEASE:$(grep "^VERSION_ID=" /etc/os-release | cut -d= -f2 | tr -d \")"
          elif [ -f /etc/redhat-release ]; then
            echo "OS_NAME:$(cat /etc/redhat-release | cut -d" " -f1)"
            echo "OS_VERSION:$(cat /etc/redhat-release | cut -d" " -f3)"
            echo "OS_RELEASE:$(cat /etc/redhat-release | cut -d" " -f4)"
          else
            echo "OS_NAME:Unknown"
            echo "OS_VERSION:Unknown"
            echo "OS_RELEASE:Unknown"
          fi
          echo "KERNEL:$(uname -r)"
          echo "ARCH:$(uname -m)"
        '
        """
        return self._execute_status_command(cmd, self._os_key_map)
    
    def network_info(self):
        """
        Get network-related information including all interfaces and their IP addresses.
        
        Returns:
            Dict containing:
            - hostname: System hostname
            - interfaces: List of dicts with interface details (name, ip_addresses, etc.)
        """
        # Use bash -c '...' with awk "..." and escaped \$ inside awk
        cmd = r"""
        bash -c '
          echo "HOSTNAME:$(hostname)"
          # Get all interfaces and their IPs
          for iface in $(ls /sys/class/net); do
            ips=$(ip -4 addr show $iface | awk "/inet /{print \$2}" | tr "\n" " ")
            echo "IFACE:$iface|IPS:$ips"
          done
        '
        """
        result = self._execute_status_command(cmd, self._network_key_map)
        
        # Parse interface information
        interfaces = []
        for line in result.get('raw_output', '').splitlines():
            if line.startswith('IFACE:'):
                iface_part, ips_part = line.split('|')
                iface_name = iface_part.split(':')[1]
                ips = ips_part.split(':')[1].strip().split()
                interfaces.append({
                    'name': iface_name,
                    'ip_addresses': ips
                })
        
        result['interfaces'] = interfaces
        return result
    
    def disk_info(self):
        """
        Get disk-related information (usage, free space, filesystem type, etc.).
        
        Returns:
            Dict containing:
            - disk_total: Total disk space (human readable)
            - disk_free: Free disk space (human readable)
            - filesystem: Filesystem type (e.g. ext4, xfs)
        """
        cmd = r"""
        bash -c '
          echo "DISK_TOTAL:$(df -h / | awk "NR==2{print \$2}")"
          echo "DISK_FREE:$(df -h / | awk "NR==2{print \$4}")"
          echo "FILESYSTEM:$(df -T / | awk "NR==2{print \$2}")"
        '
        """
        return self._execute_status_command(cmd, self._disk_key_map)
    
    def user_status(self):
        """
        Get user-related information including OS type (username, working directory, local time with timezone offset).
        
        Returns:
            Dict containing:
            - user: Username
            - cwd: Current working directory
            - time: Local time with timezone offset in ISO 8601 format (e.g., "2023-10-05T14:30:45+02:00")
            - os_type: Operating system type (e.g., "Linux")
        """
        cmd = r"""
        bash -c '
          echo "USER:$(whoami)"
          echo "CWD:$(pwd)"
          echo "TIME:$(date --iso-8601=seconds)"  # Explicit ISO format with timezone offset
          echo "OS_TYPE:$(uname -s)"  # Get OS type (Linux, Windows, etc.)
        '
        """
        return self._execute_status_command(cmd, self._user_key_map)
    
    def full_status(self):
        """
        Return a combined snapshot of system state by calling individual methods.
        
        Returns:
            Dict containing all system status information. Includes an 'errors' key
            if any component failed to retrieve its data.
        """
        status_info = {}
        errors = {}
        
        # Define components, their fetch functions, and their key maps
        components = {
            'user': (self.user_status, self._user_key_map),
            'hardware': (self.hardware_info, self._hardware_key_map),
            'network': (self.network_info, self._network_key_map),
            'disk': (self.disk_info, self._disk_key_map),
            'os': (self.os_info, self._os_key_map),
        }

        for name, (func, key_map) in components.items():
            try:
                component_info = func()
                if 'error' in component_info:
                    errors[name] = component_info['error']
                    # Add 'n/a' for expected keys if component failed, using the map
                    for key in key_map.values():
                         status_info.setdefault(key, 'n/a') # setdefault avoids overwriting if already present
                    # Optionally remove the 'error' key from the component dict before merging
                    # component_info.pop('error', None) 
                    # status_info.update(component_info) # Merge remaining keys (which should be n/a)
                else:
                    status_info.update(component_info)
            except Exception as e:
                # Catch exceptions raised by _execute_status_command (like BusyError)
                # or unexpected errors within the component function itself.
                self.logger.warning(f"Failed to get {name} status component: {e}", exc_info=True)
                errors[name] = str(e)
                # Add 'n/a' for expected keys if component failed
                for key in key_map.values():
                     status_info.setdefault(key, 'n/a')

        if errors:
            status_info['errors'] = errors # Add an 'errors' key if any component failed

        return status_info

    # Helper key maps (defined once for reuse)
    _user_key_map = {'USER': 'user', 'CWD': 'cwd', 'TIME': 'time', 'OS_TYPE': 'os_type'}
    _hardware_key_map = {
        'CPU': 'cpu_count',
        'CPU_MODEL': 'cpu_model', 
        'CPU_MHZ': 'cpu_mhz',
        'MEM_TOTAL': 'mem_total_mb',
        'MEM_FREE': 'mem_free_mb',
        'MEM_AVAIL': 'mem_available_mb',
        'LOAD': 'load_avg'
    }
    _os_key_map = {
        'OS_NAME': 'os_name',
        'OS_VERSION': 'os_version',
        'OS_RELEASE': 'os_release',
        'KERNEL': 'kernel',
        'ARCH': 'architecture'
    }
    _network_key_map = {
        'HOSTNAME': 'hostname', 
        'IFACE': 'raw_output'  # Temporary storage for parsing
    }
    _disk_key_map = {
        'DISK_TOTAL': 'disk_total',
        'DISK_FREE': 'disk_free',
        'FILESYSTEM': 'filesystem'
    }


    def _execute_status_command(self, cmd, key_map):
        """
        Execute a status command and parse its output.
        
        Args:
            cmd: The command to execute.
            key_map: Mapping of output keys to result keys.
            
        Returns:
            Dict containing parsed status information or {'error': ...}.
            Ensures all keys from key_map.values() are present, defaulting to 'n/a'.
        """
        status_info = {}
        # Populate with n/a initially to ensure all keys exist even on failure
        for key in key_map.values():
            status_info[key] = 'n/a'
            
        try:
            # Use run_ops directly instead of self.ssh_client.run to avoid circular dependency potential
            # and potentially simplify debugging if run() adds more logic later.
            handle = self.ssh_client.run_ops.execute_command(cmd.strip(), io_timeout=5, runtime_timeout=10)
            
            # Check command exit code *after* execution completes
            # Note: execute_command raises CommandFailed on non-zero exit, so this check
            # might seem redundant, but it's good practice if execute_command behavior changes.
            # However, the current implementation means we'll likely catch CommandFailed in the except block.
            # Let's adjust the try/except structure slightly.

            # Proceed with parsing if exit code is 0 (which it must be if CommandFailed wasn't raised)
            output = "".join(handle.tail(handle.total_lines)) # Get all lines
            parsed_keys = set()
            for line in output.splitlines():
                if ':' in line:
                    key, value = line.split(':', 1) # Split only on the first colon
                    clean_key = key.strip()
                    if clean_key in key_map:
                        result_key = key_map[clean_key]
                        processed_value = value.strip() # Get the raw value first
                        
                        # Specifically lowercase the 'os_type' field
                        if result_key == 'os_type':
                            status_info[result_key] = processed_value.lower()
                        else:
                            status_info[result_key] = processed_value
                            
                        parsed_keys.add(result_key)
            
            # Check if all expected keys were found in the output (after successful command execution)
            missing_keys = set(key_map.values()) - parsed_keys
            if missing_keys:
                 self.logger.warning(f"Missing expected keys in status output for command '{cmd.strip()}': {missing_keys}")
                 # Values for missing keys remain 'n/a' as set initially

        except CommandFailed as e:
             # Handle non-zero exit codes specifically
             stderr_sample = e.stderr if isinstance(e.stderr, str) else e.stderr.decode('utf-8', errors='replace')
             error_msg = f"Command failed with exit code {e.exit_code}. Stderr: {stderr_sample[:200]}"
             self.logger.warning(f"Status command failed: {cmd.strip()} - {error_msg}")
             status_info['error'] = error_msg 
             # Keep n/a values populated before

        except BusyError:
            self.logger.warning("Cannot get status: client is busy.")
            status_info['error'] = "Client is busy" # Add error key
            # Keep n/a values
            # Re-raise BusyError as it indicates a specific client state
            raise 
        except Exception as e:
            # Catch other potential errors (timeouts, connection issues, unexpected parsing errors)
            self.logger.warning(f"Failed to execute or parse status component: {e}", exc_info=True)
            status_info['error'] = str(e) # Add error key
            # Keep n/a values
        
        return status_info

    # Keep the old status() method signature but make it call full_status()
    # and add a deprecation warning or note in the docstring.
    def status(self) -> Dict[str, Any]:  # Alias for full_status
        """
        Return a combined snapshot of system state.
        DEPRECATED: Prefer calling individual methods (user_status, hardware_info, etc.)
        or use full_status() for the combined dictionary. This method now calls full_status().
        
        Returns:
            Dict containing all system status information.
        """
        self.logger.debug("Called deprecated status() method, redirecting to full_status().")
        return self.full_status()
