# Contributing to cygnus-ssh-mcp

## Releasing a New Version

### 1. Update Version

Edit `pyproject.toml` and update the version number:

```toml
[project]
version = "X.Y.Z"
```

Follow [Semantic Versioning](https://semver.org/):
- **MAJOR** (X): Breaking changes
- **MINOR** (Y): New features, backwards compatible
- **PATCH** (Z): Bug fixes, backwards compatible

### 2. Commit and Tag

```bash
git add pyproject.toml
git commit -m "release: vX.Y.Z"
git tag vX.Y.Z
git push && git push --tags
```

### 3. Build

```bash
pip install build
python -m build
```

This creates:
- `dist/cygnus_ssh_mcp-X.Y.Z-py3-none-any.whl`
- `dist/cygnus_ssh_mcp-X.Y.Z.tar.gz`

### 4. Publish to PyPI

```bash
pip install twine
twine upload dist/*
```

You'll be prompted for credentials (see PyPI Token Setup below).

### 5. Clean Up

```bash
rm -rf dist/ build/ *.egg-info/
```

## PyPI Token Setup (First Time)

1. Create an account at [pypi.org](https://pypi.org/account/register/)
2. Generate an API token at [pypi.org/manage/account/token/](https://pypi.org/manage/account/token/)
3. Create `~/.pypirc`:

```ini
[pypi]
username = __token__
password = pypi-YOUR_TOKEN_HERE
```

4. Secure the file:

```bash
chmod 600 ~/.pypirc
```

## Development Setup

```bash
git clone https://github.com/cygnussystems/cygnus-ssh-mcp.git
cd cygnus-ssh-mcp
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e .
```

## Running Tests

```bash
pip install pytest
python -m pytest testing_mcp/ -v
```

Note: Tests require a configured SSH test server (see `CLAUDE.md` for test environment setup).
