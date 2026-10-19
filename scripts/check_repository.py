"""Reject tracked private inputs/runtime data; never open credential files."""
from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath


def main() -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8").split("\0")
    forbidden = []
    for name in filter(None, tracked):
        path = PurePosixPath(name)
        if (
            path.parts[0] in {"crown_letters", "data", ".claude", ".codex", ".cursor"}
            or path.suffix.lower() in {".pdf", ".db", ".sqlite", ".sqlite3"}
            or path.name.startswith(".env") and name != ".env.example"
        ):
            forbidden.append(name)
    if forbidden:
        print("Private source/credential/runtime paths are tracked:")
        print("\n".join(forbidden))
        return 1
    print("Repository privacy check passed (tracked paths only; credentials unopened).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
