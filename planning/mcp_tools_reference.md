MCP SSH Server - Tool Reference


CONNECTION MANAGEMENT

- list_tools - Retrieves a list of all available tools on this MCP server with their descriptions
- ssh_conn_is_connected - Check if there is an active SSH connection
- ssh_conn_connect - Establish an SSH connection using a pre-configured host from TOML config
- ssh_conn_add_host - Add or update a host configuration in the host configuration TOML file
- ssh_conn_status - Get essential SSH connection status info (user, directory, OS type)
- ssh_conn_host_info - Get detailed SSH connection status and system information
- ssh_conn_verify_sudo - Verify if sudo access is available on the remote system


HOST CONFIGURATION

- ssh_host_list - List all configured SSH hosts and config file location
- ssh_host_remove - Remove a host configuration from the host configuration TOML file
- ssh_host_reload_config - Force reload of the hosts configuration file (TOML)
- ssh_host_disconnect - Disconnect the current SSH connection if one exists


COMMAND EXECUTION

- ssh_cmd_run - Execute a command on the remote host with timeout management and status tracking
- ssh_cmd_kill - Terminate a currently running command by its handle ID
- ssh_cmd_check_status - Wait for a specified duration and then check the status of a command
- ssh_cmd_output - Retrieve output from a specific command execution
- ssh_cmd_clear_history - Clear the command history for the current SSH connection
- ssh_cmd_history - Retrieve command execution history with optional output snippets and filtering


TASK MANAGEMENT

- ssh_task_launch - Launch a command in the background and return its PID
- ssh_task_status - Check the status of a background task by PID
- ssh_task_kill - Terminate a background task by sending a signal to its PID


DIRECTORY OPERATIONS

- ssh_dir_mkdir - Create a directory on the remote system with specified permissions
- ssh_dir_remove - Remove a directory on the remote system
- ssh_dir_list_files_basic - List contents of a directory on the remote system
- ssh_dir_list_advanced - List contents of a directory recursively with detailed information
- ssh_dir_search_glob - Recursively search for files matching a glob pattern
- ssh_dir_search_files_content - Search for text patterns in files of a given directory
- ssh_dir_calc_size - Calculate the total size of a directory recursively
- ssh_dir_delete - Delete a directory and all its contents recursively
- ssh_dir_batch_delete_files - Delete all files matching a pattern under a directory
- ssh_dir_copy - Copy a directory recursively


FILE OPERATIONS - READ / SEARCH

- ssh_file_stat - Get status information about a file or directory
- ssh_file_find_lines_with_pattern - Search for a pattern in a remote file and return matching lines
- ssh_file_get_context_around_line - Get lines before and after a line that matches exactly


FILE OPERATIONS - EDIT

- ssh_file_write - Create a new file or overwrite/append to an existing file with content
- ssh_file_replace_line - Replace a unique line in a file with a new line
- ssh_file_replace_line_multi - Replace a unique line in a file with multiple new lines
- ssh_file_insert_lines_after_match - Insert lines after a unique line match
- ssh_file_delete_line_by_content - Delete a line matching a unique content string


FILE OPERATIONS - TRANSFER / MANAGEMENT

- ssh_file_transfer - Transfer files between local and remote systems (upload/download)
- ssh_file_copy - Copy a file with optional timestamp appended to the destination
- ssh_file_move - Move or rename a file or directory


ARCHIVE OPERATIONS

- ssh_archive_create - Create a compressed archive from a directory (tar.gz or tar format)
- ssh_archive_extract - Extract a tar or tar.gz archive to a directory
