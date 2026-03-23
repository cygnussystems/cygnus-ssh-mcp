# Releasing cygnus-ssh-mcp

## Automated Publishing (via GitHub Actions)

Publishing to PyPI is automated. When you push a version tag, GitHub Actions builds and publishes the package.

### Release Process

1. **Update version** in `pyproject.toml`:

   ```toml
   [project]
   version = "X.Y.Z"
   ```

   Follow [Semantic Versioning](https://semver.org/):
   - **MAJOR** (X): Breaking changes
   - **MINOR** (Y): New features, backwards compatible
   - **PATCH** (Z): Bug fixes, backwards compatible

2. **Commit, tag, and push**:

   ```bash
   git add pyproject.toml
   git commit -m "release: vX.Y.Z"
   git tag vX.Y.Z
   git push && git push --tags
   ```

3. **Done!** GitHub Actions will automatically:
   - Build the package
   - Publish to PyPI

   Monitor progress at: https://github.com/cygnussystems/cygnus-ssh-mcp/actions

## First-Time Setup: PyPI Trusted Publisher

Before automated publishing works, you must configure PyPI to trust this repository:

1. Go to https://pypi.org/manage/project/cygnus-ssh-mcp/settings/publishing/
2. Click "Add a new publisher"
3. Fill in:
   - **Owner**: `cygnussystems`
   - **Repository name**: `cygnus-ssh-mcp`
   - **Workflow name**: `publish.yml`
   - **Environment name**: (leave blank)
4. Click "Add"

This uses OpenID Connect (OIDC) for secure authentication - no API tokens needed.

## Manual Publishing (Fallback)

If GitHub Actions fails or you need to publish manually:

```bash
# Install tools
pip install build twine

# Build
python -m build

# Publish (requires ~/.pypirc with API token)
twine upload dist/*

# Clean up
rm -rf dist/ build/ *.egg-info/
```

### PyPI Token Setup (for manual publishing)

1. Generate token at https://pypi.org/manage/account/token/
2. Create `~/.pypirc`:

   ```ini
   [pypi]
   username = __token__
   password = pypi-YOUR_TOKEN_HERE
   ```

3. Secure the file: `chmod 600 ~/.pypirc`

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
