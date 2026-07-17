"""Environment checks that fail with a sentence instead of a stack trace.

The two ways a fresh install breaks, and what the user sees without this:

* **Python < 3.10** — the models annotate ``str | None``. ``from __future__
  import annotations`` defers *storing* that, but pydantic resolves annotations
  at class-creation time, so it evaluates ``str | None`` anyway and raises
  ``TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`` from
  inside pydantic. Nothing in that names the Python version.
* **pydantic 1** — imports fine, then dies later on ``model_dump_json`` with an
  ``AttributeError``, by which point the user is looking at serialisation code.

Both are environment problems, so they should be reported as environment
problems, at the entry point, before any work happens.
"""
from __future__ import annotations

import sys

MIN_PYTHON = (3, 10)


def check(*, exit_on_error: bool = True) -> list[str]:
    """Return a list of problems; empty means the environment is usable."""
    problems: list[str] = []

    if sys.version_info < MIN_PYTHON:
        have = ".".join(str(n) for n in sys.version_info[:3])
        need = ".".join(str(n) for n in MIN_PYTHON)
        problems.append(
            f"Python {need}+ required, this is {have} ({sys.executable}).\n"
            f"  The data models use `str | None`, which older Pythons cannot "
            f"evaluate.\n"
            f"  Point NL-Miner at a newer interpreter (Obsidian: Settings → "
            f"NL-Miner → Python interpreter)."
        )

    try:
        import pydantic
    except ModuleNotFoundError:
        problems.append(
            f"pydantic is not installed for this interpreter "
            f"({sys.executable}).\n"
            f"  Install it:  {sys.executable} -m pip install 'pydantic>=2,<3'"
        )
    else:
        major = int(pydantic.VERSION.split(".")[0])
        if major < 2:
            problems.append(
                f"pydantic 2 required, found {pydantic.VERSION}.\n"
                f"  Upgrade:  {sys.executable} -m pip install -U "
                f"'pydantic>=2,<3'"
            )

    if problems and exit_on_error:
        print("NL-Miner cannot run in this environment:\n", file=sys.stderr)
        for p in problems:
            print(f"- {p}\n", file=sys.stderr)
        raise SystemExit(2)
    return problems
