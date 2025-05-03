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
        Get hardware-related information (CPU, memory, etc.).
        
        Returns:
            Dict containing hardware information.
        """
        # Use single quotes around awk scripts for robustness
        cmd = r"""
        bash -c '
          echo "CPU:$(grep -c ^processor /proc/cpuinfo)"
          echo "MEM_TOTAL:$(free -m | awk '/^Mem:/{print $2}')"
          echo "MEM_FREE:$(free -m | awk '/^Mem:/{print $4}')"
          echo "MEM_AVAIL:$(free -m | awk '/^Mem:/{print $7}')"
          echo "LOAD:$(cut -d" " -f1-3 /proc/loadavg 2>/dev/null || echo n/a)"
        '
        """
        return self._execute_status_command(cmd, self._hardware_key_map)
    
    def network_info(self):
        """
        Get network-related information (interfaces, IPs, etc.).
        
        Returns:
            Dict containing network information.
        """
        # Use single quotes around awk script
        cmd = r"""
        bash -c '
          echo "HOSTNAME:$(hostname)"
          echo "IP:$(hostname -I | awk '{print $1}' 2>/dev/null || echo n/a)"
        '
        """
        return self._execute_status_command(cmd, self._network_key_map)
    
    def disk_info(self):
        """
        Get disk-related information (usage, free space, etc.).
        
        Returns:
            Dict containing disk information.
        """
        # Use single quotes around awk scripts
        cmd = r"""
        bash -c '
          echo "DISK_TOTAL:$(df -h / | awk 'NR==2{print $2}')"
          echo "DISK_FREE:$(df -h / | awk 'NR==2{print $4}')"
        '
        """
        return self._execute_status_command(cmd, self._disk_key_map)
    
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
                self.logger.warning(f"Failed to get {name} status component: {e}", exc_info=True)
                errors[name] = str(e)
                # Add 'n/a' for expected keys if component failed
                for key in key_map.values():
                     status_info.setdefault(key, 'n/a')

        if errors:
            status_info['errors'] = errors # Add an 'errors' key if any component failed

        return status_info

    # Helper key maps (defined once for reuse)
    _user_key_map = {'USER': 'user', 'CWD': 'cwd', 'TIME': 'time'}
    _hardware_key_map = {'CPU': 'cpu_count', 'MEM_TOTAL': 'mem_total_mb', 'MEM_FREE': 'mem_free_mb', 'MEM_AVAIL': 'mem_available_mb', 'LOAD': 'load_avg'}
    _network_key_map = {'HOSTNAME': 'hostname', 'IP': 'ip_address'}
    _disk_key_map = {'DISK_TOTAL': 'disk_total', 'DISK_FREE': 'disk_free'}


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
            handle = self.ssh_client.run(cmd.strip(), io_timeout=5, runtime_timeout=10)
            
            # Check command exit code *before* parsing output
            if handle.exit_code != 0:
                 # Attempt to get stderr if available, otherwise use generic message
                 stderr_sample = "n/a"
                 if hasattr(handle, 'stderr_buffer') and handle.stderr_buffer:
                     stderr_sample = "".join(handle.stderr_buffer)
                 elif hasattr(handle, 'stderr') and handle.stderr: # Fallback for simpler handles
                     stderr_sample = handle.stderr
                     
                 error_msg = f"Command failed with exit code {handle.exit_code}. Stderr: {stderr_sample[:200]}"
                 self.logger.warning(f"Status command failed: {cmd.strip()} - {error_msg}")
                 # Return specific error, keeping the n/a values populated before
                 status_info['error'] = error_msg 
                 return status_info # Return early on command failure

            # Proceed with parsing if exit code is 0
            output = "".join(handle.tail(handle.total_lines)) # Get all lines
            parsed_keys = set()
            for line in output.splitlines():
                if ':' in line:
                    key, value = line.split(':', 1) # Split only on the first colon
                    clean_key = key.strip()
                    if clean_key in key_map:
                        result_key = key_map[clean_key]
                        status_info[result_key] = value.strip() # Overwrite 'n/a' with actual value
                        parsed_keys.add(result_key)
            
            # Check if all expected keys were found in the output (after successful command execution)
            missing_keys = set(key_map.values()) - parsed_keys
            if missing_keys:
                 self.logger.warning(f"Missing expected keys in status output for command '{cmd.strip()}': {missing_keys}")
                 # Values for missing keys remain 'n/a' as set initially

        except BusyError:
            self.logger.warning("Cannot get status: client is busy.")
            status_info['error'] = "Client is busy" # Add error key
            # Keep n/a values
            # Re-raise BusyError as it indicates a specific client state,
            # allowing the caller (like full_status) to handle it if needed,
            # although full_status currently catches generic Exception.
            raise 
        except Exception as e:
            self.logger.warning(f"Failed to execute or parse status component: {e}", exc_info=True)
            status_info['error'] = str(e) # Add error key
            # Keep n/a values
        
        return status_info

    # Keep the old status() method signature but make it call full_status()
    # and add a deprecation warning or note in the docstring.
    def status(self):
        """
        Return a combined snapshot of system state.
        DEPRECATED: Prefer calling individual methods (user_status, hardware_info, etc.)
        or use full_status() for the combined dictionary. This method now calls full_status().
        
        Returns:
            Dict containing all system status information.
        """
        self.logger.debug("Called deprecated status() method, redirecting to full_status().")
        return self.full_status()
