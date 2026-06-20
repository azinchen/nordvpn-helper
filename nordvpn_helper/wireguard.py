"""Read the live WireGuard/NordLynx configuration from the kernel."""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from .proc import run

# NordVPN's default resolver addresses, used when we cannot read them live.
_NORD_DNS = ["103.86.96.100", "103.86.99.100"]


def interface_exists(interface: str) -> bool:
    proc = run(["ip", "link", "show", interface], check=False)
    return proc.returncode == 0


def _showconf(interface: str) -> Dict[str, Dict[str, str]]:
    """Parse `wg showconf` into {'interface': {...}, 'peer': {...}}."""
    proc = run(["wg", "showconf", interface])
    section: Optional[str] = None
    parsed: Dict[str, Dict[str, str]] = {"interface": {}, "peer": {}}
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower() == "[interface]":
            section = "interface"
            continue
        if line.lower() == "[peer]":
            section = "peer"
            continue
        if section and "=" in line:
            key, _, value = line.partition("=")
            parsed[section][key.strip()] = value.strip()
    return parsed


def _addresses(interface: str) -> List[str]:
    """Return the local addresses assigned to the interface (with prefix len)."""
    proc = run(["ip", "-j", "addr", "show", interface], check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    addresses: List[str] = []
    try:
        for link in json.loads(proc.stdout):
            for addr in link.get("addr_info", []):
                local = addr.get("local")
                prefix = addr.get("prefixlen")
                if local and prefix is not None:
                    addresses.append(f"{local}/{prefix}")
    except (json.JSONDecodeError, AttributeError):
        return []
    return addresses


def _dns() -> List[str]:
    """Best-effort read of the resolver addresses in use."""
    servers: List[str] = []
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        servers.append(parts[1])
    except OSError:
        pass
    return servers or _NORD_DNS


def collect(interface: str, status: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """Assemble a structured description of the active WireGuard tunnel."""
    conf = _showconf(interface)
    iface = conf["interface"]
    peer = conf["peer"]
    status = status or {}

    return {
        "interface": interface,
        "address": _addresses(interface),
        "dns": _dns(),
        "private_key": iface.get("PrivateKey", ""),
        "listen_port": iface.get("ListenPort", ""),
        "peer": {
            "public_key": peer.get("PublicKey", ""),
            "endpoint": peer.get("Endpoint", ""),
            "allowed_ips": peer.get("AllowedIPs", ""),
            "persistent_keepalive": peer.get("PersistentKeepalive", ""),
        },
        "server": {
            "hostname": status.get("hostname", ""),
            "ip": status.get("ip", ""),
            "country": status.get("country", ""),
            "city": status.get("city", ""),
        },
    }


def format_json(data: Dict[str, object]) -> str:
    return json.dumps(data, indent=2)


def format_text(data: Dict[str, object]) -> str:
    """Render a ready-to-use WireGuard config file from collected data."""
    peer = data["peer"]  # type: ignore[index]
    address = ", ".join(data["address"]) or "(unknown)"  # type: ignore[arg-type]
    dns = ", ".join(data["dns"])  # type: ignore[arg-type]

    lines = [
        "[Interface]",
        f"PrivateKey = {data['private_key']}",
        f"Address = {address}",
        f"DNS = {dns}",
    ]
    if data["listen_port"]:
        lines.append(f"ListenPort = {data['listen_port']}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {peer['public_key']}",
        f"Endpoint = {peer['endpoint']}",
        f"AllowedIPs = {peer['allowed_ips'] or '0.0.0.0/0, ::/0'}",
    ]
    if peer["persistent_keepalive"]:
        lines.append(f"PersistentKeepalive = {peer['persistent_keepalive']}")
    return "\n".join(lines)
