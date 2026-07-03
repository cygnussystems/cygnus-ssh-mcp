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
    """
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return f'powershell -NoProfile -EncodedCommand {encoded}'
