import base64


def powershell_encoded_command(script: str) -> str:
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
    """
    full_script = "$ProgressPreference = 'SilentlyContinue'\n" + script
    encoded = base64.b64encode(full_script.encode('utf-16-le')).decode('ascii')
    return f'powershell -NoProfile -EncodedCommand {encoded}'
