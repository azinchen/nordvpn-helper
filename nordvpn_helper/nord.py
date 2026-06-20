"""Thin wrappers around the `nordvpn` CLI used by the helper commands."""

from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional, Sequence

from .proc import CommandError, log, run


def login() -> None:
    """Authenticate the daemon with the token from the environment.

    Token login (``nordvpn login --token``) is the only non-interactive method
    the current client supports — username/password login has been removed, and
    the default flow opens a browser.
    """
    token = os.environ.get("NORDVPN_TOKEN", "").strip()

    if is_logged_in():
        log("already logged in; reusing existing session")
        return

    if not token:
        raise RuntimeError("no credentials provided: set NORDVPN_TOKEN")

    proc = run(["nordvpn", "login", "--token", token], check=False)
    out = (proc.stdout + proc.stderr).lower()
    if not (proc.returncode == 0 or "welcome" in out or "already logged in" in out):
        raise RuntimeError(f"token login failed: {(proc.stderr or proc.stdout).strip()}")

    if not is_logged_in():
        raise RuntimeError("login appeared to succeed but the daemon is not logged in")
    log("logged in")


def is_logged_in() -> bool:
    proc = run(["nordvpn", "account"], check=False)
    return proc.returncode == 0 and "not logged in" not in proc.stdout.lower()


def set_technology(technology: str = "nordlynx") -> None:
    """Select the VPN technology. NordLynx == WireGuard."""
    run(["nordvpn", "set", "technology", technology])
    log(f"technology set to {technology}")


def connect(target: Optional[Sequence[str]] = None) -> None:
    """Connect to a NordVPN server.

    With no target NordVPN picks the recommended server; otherwise ``target`` is
    passed straight to ``nordvpn connect`` and may be a country, city, server,
    group, or "country city" pair (e.g. ["United_States", "Chicago"]).
    """
    target = list(target or [])
    proc = run(["nordvpn", "connect", *target], check=False, timeout=120)
    out = (proc.stdout + proc.stderr).lower()
    if proc.returncode == 0 or "you are connected" in out:
        log("connected to " + (" ".join(target) if target else "recommended server"))
        return
    where = f" to {' '.join(target)}" if target else ""
    raise RuntimeError(f"connect{where} failed: {(proc.stderr or proc.stdout).strip()}")


def disconnect() -> None:
    try:
        run(["nordvpn", "disconnect"], check=False)
        log("disconnected")
    except CommandError as exc:  # best effort during cleanup
        log(f"disconnect failed (ignored): {exc}")


def status() -> Dict[str, str]:
    """Return `nordvpn status` parsed into a key -> value mapping."""
    return parse_key_values(run(["nordvpn", "status"], check=False).stdout)


def run_cli(args: Sequence[str], *, login_required: bool = False) -> str:
    """Run an informational `nordvpn` subcommand and return its cleaned output.

    Used by the read-only commands (countries, cities, groups, account,
    settings, status). Pass ``login_required`` for subcommands that the daemon
    only answers once authenticated.
    """
    if login_required:
        login()
    return run(["nordvpn", *args]).stdout.strip()


def parse_key_values(text: str) -> Dict[str, str]:
    """Parse `key: value` style CLI output into a mapping."""
    result: Dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        # Skip lines split mid-URL (e.g. "Privacy Policy - https://...") — the
        # partition above chops them at the scheme colon, leaving a "//..." tail.
        if value.startswith("//"):
            continue
        if key and value:
            result[key] = value
    return result


def parse_list(text: str) -> List[str]:
    """Parse a comma/whitespace-separated CLI list (countries, cities, groups).

    NordVPN renders multi-word names with underscores (e.g. ``United_States``,
    ``Onion_Over_VPN``), so splitting on commas and whitespace is safe.
    """
    tokens = re.split(r"[,\s]+", text.strip())
    return sorted(token for token in (t.strip() for t in tokens) if token)


def wait_until_connected(interface: str, timeout: float = 30.0) -> None:
    """Block until `nordvpn status` reports a connection on the interface."""
    from .wireguard import interface_exists

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = status()
        if info.get("status", "").lower() == "connected" and interface_exists(interface):
            return
        time.sleep(1)
    raise RuntimeError(
        f"timed out waiting for a connection on '{interface}' "
        f"(status: {status().get('status', 'unknown')})"
    )
