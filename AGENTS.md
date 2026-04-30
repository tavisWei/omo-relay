# AGENTS.md — Agentic Coding Guidelines for omo-looper

## Build / Lint / Test Commands

| Command | Purpose |
|---------|---------|
| `pytest` | Run all tests |
| `pytest -k <name>` | Run a single test by keyword |
| `pytest tests/test_file.py::test_func` | Run one specific test |
| `ruff check .` | Lint all files |
| `ruff check --fix .` | Lint and auto-fix issues |
| `ruff format .` | Format all files |
| `mypy .` | Type-check all files |

> If the project uses a different test runner (e.g., `unittest`, `tox`, `nox`), prefer the existing `Makefile` or `pyproject.toml` scripts section.

## Code Style Guidelines

### Imports
- Use **absolute imports** over relative imports.
- Group imports in this order:
  1. Standard library
  2. Third-party packages
  3. Local application modules
- Use `isort` or `ruff` to enforce import ordering.

### Formatting
- Follow **PEP 8**.
- Use **4 spaces** for indentation.
- Max line length: **88 characters** (Black default) or **100** if configured in `pyproject.toml`.
- Use double quotes `"` for strings unless single quotes `'` avoid escaping.

### Types
- Use **type hints** for all function signatures and public APIs.
- Prefer `typing` generics (`list[str]`, `dict[str, int]`) over `typing.List`, `typing.Dict` when on Python ≥3.9.
- Avoid `Any` unless absolutely necessary; use `object` or specific protocols.

### Naming Conventions
| Construct | Convention |
|-----------|------------|
| Modules / packages | `snake_case` |
| Classes | `PascalCase` |
| Functions / methods | `snake_case` |
| Constants | `UPPER_SNAKE_CASE` |
| Private internals | `_leading_underscore` |

### Error Handling
- Catch **specific exceptions**, never bare `except:`.
- Prefer custom exception subclasses over raising generic `Exception` or `RuntimeError`.
- Use `try/except/finally` for resource cleanup or context managers (`with` statement).
- Log exceptions with context; avoid swallowing errors silently.

### General Practices
- Keep functions **small and focused** (≤30–40 lines when possible).
- Prefer **composition over inheritance**.
- Write **docstrings** for all public modules, classes, and functions (Google or NumPy style).
- Add **unit tests** for new logic; aim for high coverage on critical paths.
- Do not commit **secrets** (API keys, passwords) or `.env` files.

## Project-Specific Notes

- **Language / Stack**: Python (update if multi-language).
- **Package Manager**: `pip` / `uv` / `poetry` (check `pyproject.toml` or `requirements.txt`).
- **Test Framework**: `pytest` (default; verify with existing tests).
- **Linter / Formatter**: `ruff` (preferred) or `flake8` + `black`.
- **Type Checker**: `mypy`.

## External Rules

- No `.cursorrules` or `.cursor/rules/` found.
- No `.github/copilot-instructions.md` found.
- If any of the above are added later, merge their content into this file.

## Quick Checklist Before Committing

1. `ruff check .` passes.
2. `ruff format .` applied.
3. `mypy .` passes.
4. `pytest` passes (or at least the tests relevant to your change).
5. New code follows the naming and import conventions above.
