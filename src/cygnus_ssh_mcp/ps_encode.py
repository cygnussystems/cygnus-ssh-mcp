import base64


def powershell_encoded_command(script: str, utf8_output: bool = True) -> str:
    """Build a 'powershell -EncodedCommand ...' invocation for the given script.

    Some Windows hosts configure PowerShell (not cmd.exe) as the SSH DefaultShell.
    In that case a plain 'powershell -Command "...$var..."' string gets parsed and
    interpolated by the OUTER shell before the inner powershell.exe ever sees it,
    silently corrupting any $variable references or double-quoted expressions.
    Base64-encoding the script sidesteps outer-shell quoting/interpolation
    entirely - the payload has no shell metacharacters for cmd.exe OR PowerShell
    to misinterpret, so it works identically regardless of the remote DefaultShell.

    Prepends '$ProgressPreference = "SilentlyContinue"' to every script: PowerShell
    serializes its own progress stream (e.g. "Preparing modules for first use" on
    first cmdlet/module autoload in a session) to CLIXML on stderr whenever there's
    no interactive host to render it - which is always true here, since every
    invocation goes through this non-interactive -EncodedCommand path. Verified
    live 2026-07-04: this polluted ssh_cmd_run's stderr capture even on a plain
    'del' that never wrote to stderr itself. Suppressing the stream at the source
    is more robust than trying to strip the CLIXML envelope back out afterward.

    utf8_output (default True) also switches the script's stdout to UTF-8 (no BOM).
    Otherwise PowerShell writes in the console's OEM code page and every non-ASCII
    character in a path or name it prints is lost - "unicode-café 漢字.txt" came back
    as "unicode-caf� ??.txt" from ssh_dir_delete's item list (verified live
    2026-09-28 on Server 2016). Pass False only for a script that relays some OTHER
    program's output (ssh_cmd_run's wrapper), whose encoding it doesn't control.
    """
    prefix = "$ProgressPreference = 'SilentlyContinue'\n"
    if utf8_output:
        prefix += "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false\n"
    full_script = prefix + script
    encoded = base64.b64encode(full_script.encode('utf-16-le')).decode('ascii')
    return f'powershell -NoProfile -EncodedCommand {encoded}'
