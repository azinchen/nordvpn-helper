"""Subprocess helpers shared by the command implementations."""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Sequence

# NordVPN's CLI decorates output with ANSI colour codes and spinner control
# characters even when stdout is not a TTY. Strip them before we parse.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL_RE = re.compile(r"[\r\x08]")


def log(message: str) -> None:
    """Write a diagnostic line to stderr so it never pollutes command output."""
    print(f"[nordvpn-helper] {message}", file=sys.stderr, flush=True)


def clean(text: str) -> str:
    """Remove ANSI/spinner noise from CLI output."""
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub("\n", text)
    return text


def run(
    cmd: Sequence[str],
    *,
    check: bool = True,
    timeout: float = 90.0,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing cleaned text output.

    Raises CommandError when ``check`` is set and the command exits non-zero.
    """
    log(f"$ {' '.join(cmd)}")
    proc = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    proc.stdout = clean(proc.stdout or "")
    proc.stderr = clean(proc.stderr or "")
    if check and proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip() or "no output").strip()
        raise CommandError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{detail}"
        )
    return proc


class CommandError(RuntimeError):
    """A wrapped subprocess failed."""
