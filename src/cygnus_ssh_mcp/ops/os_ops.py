import time
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any
from cygnus_ssh_mcp.models import SshError, CommandTimeout, BusyError, CommandFailed


class SshOsOperations(ABC):
    """Base class for OS-level operations. Platform-specific commands are abstract methods."""

    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshOsOperations")

    # ==========================================================================
    # Abstract command methods - implemented by platform-specific subclasses
    # ==========================================================================

    @abstractmethod
    def _cmd_hardware_info(self) -> str:
        """Return command to get hardware info (CPU, memory, load)."""
        pass

    @abstractmethod
    def _cmd_os_info(self) -> str:
        """Return command to get OS info (name, version, kernel, arch)."""
        pass

    @abstractmethod
    def _cmd_network_info(self) -> str:
        """Return command to get network info (hostname, interfaces)."""
        pass

    @abstractmethod
    def _cmd_disk_info(self) -> str:
        """Return command to get disk info (total, free, filesystem)."""
        pass

    @abstractmethod
    def _cmd_user_status(self) -> str:
        """Return command to get user status (user, cwd, time, os_type)."""
        pass

    # ==========================================================================
    # Shared implementation methods
    # ==========================================================================

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
                    time.sleep(5)  # Wait before attempting reconnect
                    self.ssh_client._connect()  # Try to reconnect
                    self.logger.info("Reconnected successfully after reboot.")
                    reconnected = True
                    return  # Success
                except Exception as connect_err:
                    self.logger.debug(f"Reconnect attempt failed: {connect_err}")
                    # Continue waiting

            # If loop finishes without reconnecting
            if not reconnected:
                self.logger.error(f"Host did not come back online within {timeout} seconds.")
                raise CommandTimeout(timeout)  # Raise timeout error specifically

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
        cmd = self._cmd_hardware_info()
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
        cmd = self._cmd_os_info()
        return self._execute_status_command(cmd, self._os_key_map)

    def network_info(self):
        """
        Get network-related information including all interfaces and their IP addresses.

        Returns:
            Dict containing:
            - hostname: System hostname
            - interfaces: List of dicts with interface details (name, ip_addresses, etc.)
        """
        cmd = self._cmd_network_info()
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
        cmd = self._cmd_disk_info()
        return self._execute_status_command(cmd, self._disk_key_map)

    def user_status(self):
        """
        Get user-related information including OS type.

        Returns:
            Dict containing:
            - user: Username
            - cwd: Current working directory
            - time: Local time with timezone offset in ISO 8601 format
            - os_type: Operating system type (e.g., "linux", "darwin")
        """
        cmd = self._cmd_user_status()
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
                        status_info.setdefault(key, 'n/a')
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
            status_info['errors'] = errors  # Add an 'errors' key if any component failed

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
            handle = self.ssh_client.run_ops.execute_command(cmd.strip(), io_timeout=5, runtime_timeout=10)

            # Proceed with parsing if exit code is 0 (which it must be if CommandFailed wasn't raised)
            output = "".join(handle.tail(handle.total_lines))  # Get all lines
            parsed_keys = set()
            for line in output.splitlines():
                if ':' in line:
                    key, value = line.split(':', 1)  # Split only on the first colon
                    clean_key = key.strip()
                    if clean_key in key_map:
                        result_key = key_map[clean_key]
                        processed_value = value.strip()  # Get the raw value first

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
            status_info['error'] = "Client is busy"
            # Re-raise BusyError as it indicates a specific client state
            raise
        except Exception as e:
            # Catch other potential errors (timeouts, connection issues, unexpected parsing errors)
            self.logger.warning(f"Failed to execute or parse status component: {e}", exc_info=True)
            status_info['error'] = str(e)
            # Keep n/a values

        return status_info

    def status(self) -> Dict[str, Any]:
        """
        Return a combined snapshot of system state.
        DEPRECATED: Prefer calling individual methods (user_status, hardware_info, etc.)
        or use full_status() for the combined dictionary.

        Returns:
            Dict containing all system status information.
        """
        self.logger.debug("Called deprecated status() method, redirecting to full_status().")
        return self.full_status()


class SshOsOperations_Linux(SshOsOperations):
    """Linux implementation of OS operations using /proc, /sys, and GNU coreutils."""

    def _cmd_hardware_info(self) -> str:
        """Return command to get hardware info using /proc and free."""
        return r"""
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

    def _cmd_os_info(self) -> str:
        """Return command to get OS info using /etc/os-release."""
        return r"""
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

    def _cmd_network_info(self) -> str:
        """Return command to get network info using /sys/class/net and ip."""
        return r"""
        bash -c '
          echo "HOSTNAME:$(hostname)"
          # Get all interfaces and their IPs
          for iface in $(ls /sys/class/net); do
            ips=$(ip -4 addr show $iface | awk "/inet /{print \$2}" | tr "\n" " ")
            echo "IFACE:$iface|IPS:$ips"
          done
        '
        """

    def _cmd_disk_info(self) -> str:
        """Return command to get disk info using df."""
        return r"""
        bash -c '
          echo "DISK_TOTAL:$(df -h / | awk "NR==2{print \$2}")"
          echo "DISK_FREE:$(df -h / | awk "NR==2{print \$4}")"
          echo "FILESYSTEM:$(df -T / | awk "NR==2{print \$2}")"
        '
        """

    def _cmd_user_status(self) -> str:
        """Return command to get user status using GNU date."""
        return r"""
        bash -c '
          echo "USER:$(whoami)"
          echo "CWD:$(pwd)"
          echo "TIME:$(date --iso-8601=seconds)"
          echo "OS_TYPE:$(uname -s)"
        '
        """


class SshOsOperations_Mac(SshOsOperations):
    """macOS implementation of OS operations using sysctl, sw_vers, and BSD tools."""

    def _cmd_hardware_info(self) -> str:
        """Return command to get hardware info using sysctl and vm_stat."""
        return r"""
        bash -c '
          echo "CPU:$(sysctl -n hw.ncpu)"
          echo "CPU_MODEL:$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo n/a)"
          echo "CPU_MHZ:$(sysctl -n hw.cpufrequency 2>/dev/null | awk "{print \$1/1000000}" || echo n/a)"
          # Memory: hw.memsize gives bytes, convert to MB
          mem_bytes=$(sysctl -n hw.memsize)
          mem_mb=$((mem_bytes / 1048576))
          echo "MEM_TOTAL:$mem_mb"
          # Free/available memory from vm_stat (pages * page_size)
          page_size=$(vm_stat | head -1 | awk -F"page size of " "{print \$2}" | tr -d " bytes)")
          pages_free=$(vm_stat | awk "/Pages free:/{print \$3}" | tr -d ".")
          pages_inactive=$(vm_stat | awk "/Pages inactive:/{print \$3}" | tr -d ".")
          mem_free_mb=$(( (pages_free * page_size) / 1048576 ))
          mem_avail_mb=$(( ((pages_free + pages_inactive) * page_size) / 1048576 ))
          echo "MEM_FREE:$mem_free_mb"
          echo "MEM_AVAIL:$mem_avail_mb"
          echo "LOAD:$(sysctl -n vm.loadavg | awk "{print \$2, \$3, \$4}")"
        '
        """

    def _cmd_os_info(self) -> str:
        """Return command to get OS info using sw_vers."""
        return r"""
        bash -c '
          echo "OS_NAME:$(sw_vers -productName)"
          echo "OS_VERSION:$(sw_vers -productVersion)"
          echo "OS_RELEASE:$(sw_vers -buildVersion)"
          echo "KERNEL:$(uname -r)"
          echo "ARCH:$(uname -m)"
        '
        """

    def _cmd_network_info(self) -> str:
        """Return command to get network info using ifconfig."""
        return r"""
        bash -c '
          echo "HOSTNAME:$(hostname)"
          # Get all interfaces and their IPs using ifconfig
          for iface in $(ifconfig -l); do
            ips=$(ifconfig $iface | awk "/inet /{print \$2}" | tr "\n" " ")
            if [ -n "$ips" ]; then
              echo "IFACE:$iface|IPS:$ips"
            fi
          done
        '
        """

    def _cmd_disk_info(self) -> str:
        """Return command to get disk info using df and diskutil."""
        return r"""
        bash -c '
          echo "DISK_TOTAL:$(df -h / | awk "NR==2{print \$2}")"
          echo "DISK_FREE:$(df -h / | awk "NR==2{print \$4}")"
          # Get filesystem type from mount or diskutil
          fs_type=$(mount | grep " / " | awk -F"(" "{print \$2}" | awk -F"," "{print \$1}")
          echo "FILESYSTEM:$fs_type"
        '
        """

    def _cmd_user_status(self) -> str:
        """Return command to get user status using BSD date."""
        return r"""
        bash -c '
          echo "USER:$(whoami)"
          echo "CWD:$(pwd)"
          echo "TIME:$(date -u +%Y-%m-%dT%H:%M:%S%z)"
          echo "OS_TYPE:$(uname -s)"
        '
        """


class SshOsOperations_Win(SshOsOperations):
    """Windows implementation of OS operations using PowerShell and WMI/CIM."""

    def _cmd_hardware_info(self) -> str:
        """Return PowerShell command to get hardware info using CIM."""
        return 'powershell -Command "$cpu = Get-CimInstance Win32_Processor; $os = Get-CimInstance Win32_OperatingSystem; Write-Output \\"CPU:$($cpu.NumberOfLogicalProcessors)\\"; Write-Output \\"CPU_MODEL:$($cpu.Name)\\"; Write-Output \\"CPU_MHZ:$($cpu.MaxClockSpeed)\\"; Write-Output \\"MEM_TOTAL:$([math]::Round($os.TotalVisibleMemorySize/1024))\\"; Write-Output \\"MEM_FREE:$([math]::Round($os.FreePhysicalMemory/1024))\\"; Write-Output \\"MEM_AVAIL:$([math]::Round($os.FreePhysicalMemory/1024))\\"; Write-Output \\"LOAD:n/a\\""'

    def _cmd_os_info(self) -> str:
        """Return PowerShell command to get OS info using CIM."""
        return 'powershell -Command "$os = Get-CimInstance Win32_OperatingSystem; Write-Output \\"OS_NAME:$($os.Caption)\\"; Write-Output \\"OS_VERSION:$($os.Version)\\"; Write-Output \\"OS_RELEASE:$($os.BuildNumber)\\"; Write-Output \\"KERNEL:$($os.Version)\\"; Write-Output \\"ARCH:$env:PROCESSOR_ARCHITECTURE\\""'

    def _cmd_network_info(self) -> str:
        """Return PowerShell command to get network info."""
        return "powershell -Command \"Write-Output ('HOSTNAME:' + $env:COMPUTERNAME); Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceAlias -ne 'Loopback Pseudo-Interface 1' } | ForEach-Object { Write-Output ('IFACE:' + $_.InterfaceAlias + '|IPS:' + $_.IPAddress) }\""

    def _cmd_disk_info(self) -> str:
        """Return PowerShell command to get disk info using CIM."""
        return 'powershell -Command "$disk = Get-CimInstance Win32_LogicalDisk | Where-Object { $_.DeviceID -eq \'C:\' }; $totalGB = [math]::Round($disk.Size / 1GB, 1); $freeGB = [math]::Round($disk.FreeSpace / 1GB, 1); Write-Output \\"DISK_TOTAL:${totalGB}G\\"; Write-Output \\"DISK_FREE:${freeGB}G\\"; Write-Output \\"FILESYSTEM:$($disk.FileSystem)\\""'

    def _cmd_user_status(self) -> str:
        """Return PowerShell command to get user status."""
        return 'powershell -Command "Write-Output \\"USER:$env:USERNAME\\"; Write-Output \\"CWD:$(Get-Location)\\"; Write-Output \\"TIME:$(Get-Date -Format \'o\')\\"; Write-Output \\"OS_TYPE:Windows\\""'
