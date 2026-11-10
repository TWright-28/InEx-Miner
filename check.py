import subprocess
import sys

COMMANDS = (
    [sys.executable, "-m", "ruff", "format", "--check", "."],
    [sys.executable, "-m", "ruff", "check", "."],
    [sys.executable, "-m", "pytest", "-q"],
)


def main() -> int:
    for command in COMMANDS:
        print("$", " ".join(command[1:]), flush=True)
        code = subprocess.run(command).returncode
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
