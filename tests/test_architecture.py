import ast
import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

from conftest import ENV_VARS

REPO = Path(__file__).resolve().parents[1]

LAYER_IMPORTS = {
    "__main__": {"cli"},
    "cli": {"app", "validators", "errors", "config", "agent.chat"},
    "agent": {"app", "validators", "errors", "config", "agent"},
    "app": {"config", "services", "store", "clients", "errors"},
    "validators": {"errors"},
    "services": {"store", "clients", "errors", "services"},
    "store": {"errors", "store"},
    "clients": {"errors", "clients"},
    "config": {"errors"},
    "errors": set(),
}
THIRD_PARTY_HOMES = {
    "sqlite3": {"store"},
    "requests": {"clients"},
    "langchain": {"agent"},
    "langgraph": {"agent"},
    "dotenv": {"config"},
}
EXCEPTIONS = {
    "inex/services/collection.py": {"requests"},
    "inex/services/classification.py": {"requests", "langchain"},
}
PURE_MODULES = ("evals/scenarios.py", "evals/checks.py", "evals/report.py")
PURE_IMPORTS = {
    "yaml",
    *(Path(p).with_suffix("").as_posix().replace("/", ".") for p in PURE_MODULES),
}


NOQA = re.compile(r"^#\s*noqa\b")
ENVIRONMENT_NAMES = {"environ", "getenv"}


def _posix(path):
    return Path(path).as_posix()


def _layer(module_path):
    parts = Path(module_path).with_suffix("").parts
    return parts[1] if len(parts) > 1 else None


def _imported_names(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                yield "." * node.level + (node.module or "")
            elif node.module.split(".")[0] == "inex":
                yield from (f"{node.module}.{alias.name}" for alias in node.names)
            else:
                yield node.module


def layer_violations(module_path, source):
    path = _posix(module_path)
    layer = _layer(module_path)
    allowed = LAYER_IMPORTS.get(layer, set())
    excepted = EXCEPTIONS.get(path, set())
    found = []
    for name in _imported_names(source):
        if name.startswith("."):
            found.append(f"{path}: relative import {name}")
            continue
        parts = name.split(".")
        if parts[0] == "inex":
            if len(parts) < 2 or parts[1] == layer:
                continue
            if parts[1] in allowed or ".".join(parts[1:3]) in allowed:
                continue
            found.append(f"{path}: layer {layer} may not import {name}")
            continue
        home = "langchain" if parts[0].startswith("langchain") else parts[0]
        if home in THIRD_PARTY_HOMES and layer not in THIRD_PARTY_HOMES[home]:
            if home not in excepted:
                found.append(f"{path}: layer {layer} may not import {name}")
    return found


def purity_violations(module_path, source):
    path = _posix(module_path)
    found = []
    for name in _imported_names(source):
        top = name.split(".")[0]
        if top in sys.stdlib_module_names or top == "yaml" or name in PURE_IMPORTS:
            continue
        found.append(f"{path}: pure module may not import {name}")
    return found


def environment_violations(module_path, source):
    path = _posix(module_path)
    if path == "inex/config.py":
        return []
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr in ENVIRONMENT_NAMES
        ):
            found.append(f"{path}:{node.lineno}: os.{node.attr}")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            if {alias.name for alias in node.names} & ENVIRONMENT_NAMES:
                found.append(f"{path}:{node.lineno}: from os import environment access")
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names = (
                [node.module] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            )
            if any((name or "").split(".")[0] == "dotenv" for name in names):
                found.append(f"{path}:{node.lineno}: dotenv")
    return found


def comment_violations(path, source):
    found = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type == tokenize.COMMENT and not NOQA.match(token.string):
            found.append(f"{_posix(path)}:{token.start[0]}: comment")
    for node in ast.walk(ast.parse(source)):
        documented = isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        if documented and ast.get_docstring(node, clean=False) is not None:
            found.append(f"{_posix(path)}:{getattr(node, 'lineno', 1)}: docstring")
    return found


def inex_modules():
    return sorted((REPO / "inex").rglob("*.py"))


def python_files():
    roots = [REPO / "inex", REPO / "tests", REPO / "evals"]
    files = [p for root in roots for p in root.rglob("*.py")]
    return sorted(files + [REPO / "check.py"])


def violations_in(files, checker):
    found = []
    for path in files:
        found += checker(path.relative_to(REPO), path.read_text(encoding="utf-8"))
    return found


def test_layer_checker_catches_sqlite_in_a_service():
    assert layer_violations(Path("inex/services/x.py"), "import sqlite3\n")


def test_layer_checker_catches_store_importing_services():
    source = "from inex.services.queries import QueryService\n"
    assert layer_violations(Path("inex/store/x.py"), source)


def test_layer_checker_catches_relative_imports():
    assert layer_violations(Path("inex/services/x.py"), "from . import linkage\n")


def test_layer_checker_catches_cli_reaching_agent_tools():
    assert layer_violations(Path("inex/cli.py"), "from inex.agent.tools import build_tools\n")
    assert layer_violations(Path("inex/cli.py"), "from inex.agent.chat import run_chat\n") == []


def test_layer_checker_treats_every_langchain_package_as_langchain():
    assert layer_violations(Path("inex/services/x.py"), "import langchain_core\n")


def test_layer_checker_honours_the_exception_list():
    assert layer_violations(Path("inex/services/collection.py"), "import requests\n") == []
    assert layer_violations(Path("inex/services/collection.py"), "import langchain\n")


def test_repository_respects_the_layers():
    assert violations_in(inex_modules(), layer_violations) == []


def test_environment_checker_catches_getenv_and_environ():
    assert environment_violations(Path("inex/services/x.py"), "import os\nos.getenv('X')\n")
    assert environment_violations(Path("inex/services/x.py"), "import os\nos.environ['X']\n")
    assert environment_violations(Path("inex/services/x.py"), "from os import environ\n")
    assert environment_violations(Path("inex/config.py"), "import os\nos.environ['X']\n") == []


def test_only_config_reads_the_environment():
    assert violations_in(inex_modules(), environment_violations) == []


def test_importing_inex_creates_nothing(tmp_path):
    code = (
        "import importlib, pkgutil, inex\n"
        "[importlib.import_module(m.name) for m in pkgutil.walk_packages(inex.__path__, 'inex.')"
        " if m.name != 'inex.__main__']\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ENV_VARS}
    env["PYTHONPATH"] = str(REPO)
    subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=tmp_path, env=env, check=True)
    assert list(tmp_path.iterdir()) == []


def test_comment_checker_catches_comments_and_docstrings():
    assert comment_violations(Path("x.py"), "x = 1  # note\n")
    assert comment_violations(Path("x.py"), 'def f():\n    """doc"""\n')
    assert comment_violations(Path("x.py"), '"""module doc"""\n')
    assert comment_violations(Path("x.py"), "import os  # noqa: F401\n") == []
    assert comment_violations(Path("x.py"), 'x = "# not a comment"\n') == []


def test_no_comments_or_docstrings_anywhere():
    assert violations_in(python_files(), comment_violations) == []


def test_purity_checker_catches_langchain_in_checks():
    assert purity_violations(
        Path("evals/checks.py"), "from langchain_core.messages import AIMessage\n"
    )
    assert purity_violations(Path("evals/checks.py"), "from evals.runner import Trace\n")
    source = "import re\nimport yaml\nfrom evals.scenarios import Check\n"
    assert purity_violations(Path("evals/checks.py"), source) == []


def test_pure_eval_modules_import_only_the_standard_library_and_yaml():
    assert violations_in([REPO / p for p in PURE_MODULES], purity_violations) == []
