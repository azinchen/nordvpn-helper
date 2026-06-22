"""Client for NordVPN's public HTTP API.

The `nordvpn` CLI cannot enumerate servers, so `recommend` talks to the public
API directly. Filters are expressed as numeric ids, so we first resolve the
human-friendly country / city / technology / group names into ids, then ask the
recommendations endpoint for the best matching servers.
"""

from __future__ import annotations

import base64
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .proc import log

API_BASE = "https://api.nordvpn.com/v1"
OVPN_BASE = "https://downloads.nordcdn.com/configs/files"
TIMEOUT = 30

# Friendly technology names -> API identifiers.
_TECH_ALIASES = {
    "nordlynx": "wireguard_udp",
    "wireguard": "wireguard_udp",
    "openvpn": "openvpn_udp",
}


def _norm(text: str) -> str:
    """Normalise a name for loose matching (case/spacing/underscore agnostic)."""
    return re.sub(r"[\s_\-]+", "", text.strip().lower())


def _underscore(name: str) -> str:
    """Render a display name the way the `nordvpn` CLI did: spaces -> underscores.

    Keeps listings (``United_States``, ``Onion_Over_VPN``) compatible with the
    names other commands accept (e.g. ``cities United_States``).
    """
    return re.sub(r"\s+", "_", name.strip())


def _get(path: str, query: str = "", token: str = "") -> object:
    url = f"{API_BASE}/{path}"
    if query:
        url += "?" + query
    log(f"GET {url}")
    headers = {"User-Agent": "nordvpn-helper"}
    if token:
        # NordVPN authenticates these endpoints with HTTP basic auth, using the
        # literal username "token" and the token as the password.
        encoded = base64.b64encode(f"token:{token}".encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        hint = " (is NORDVPN_TOKEN valid?)" if exc.code in (401, 403) else ""
        raise RuntimeError(f"NordVPN API request failed ({url}): {exc}{hint}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"NordVPN API request failed ({url}): {exc}") from exc


def technologies() -> List[str]:
    """List the technology identifiers NordVPN's API knows about."""
    data = _get("technologies")
    return sorted(t["identifier"] for t in data if t.get("identifier"))


def countries() -> List[str]:
    """List the countries NordVPN has servers in (names, underscored)."""
    data = _get("servers/countries")
    return sorted(_underscore(c["name"]) for c in data if c.get("name"))


def cities(country: str) -> List[str]:
    """List the cities with servers in a country (names, underscored).

    ``country`` matches loosely on name or two-letter code. Raises LookupError
    if no such country exists.
    """
    data = _get("servers/countries")
    want = _norm(country)
    match = next(
        (c for c in data
         if _norm(c["name"]) == want or str(c.get("code", "")).lower() == want),
        None,
    )
    if match is None:
        raise LookupError(f"unknown country: {country!r}")
    return sorted(
        _underscore(ci["name"]) for ci in match.get("cities", []) if ci.get("name")
    )


def groups() -> List[str]:
    """List the server group titles (P2P, Double_VPN, regions, ...), underscored."""
    data = _get("servers/groups")
    return sorted(_underscore(g["title"]) for g in data if g.get("title"))


def hostname(server: str) -> str:
    """Normalise a server reference (e.g. "us9999") to its full hostname."""
    server = server.strip()
    return server if server.endswith(".nordvpn.com") else f"{server}.nordvpn.com"


def openvpn_config(server: str, protocol: str = "udp") -> str:
    """Download the OpenVPN (.ovpn) config for a specific server.

    These are static public files (no auth needed). ``protocol`` is "udp" or
    "tcp". Raises LookupError if the server has no such config (HTTP 404).
    """
    if protocol not in ("udp", "tcp"):
        raise ValueError(f"protocol must be 'udp' or 'tcp', got {protocol!r}")
    host = hostname(server)
    url = f"{OVPN_BASE}/ovpn_{protocol}/servers/{host}.{protocol}.ovpn"
    log(f"GET {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "nordvpn-helper"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LookupError(
                f"no OpenVPN config for server {server!r} (protocol {protocol}); "
                "check the hostname") from exc
        raise RuntimeError(f"OpenVPN config download failed ({url}): {exc}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"OpenVPN config download failed ({url}): {exc}") from exc


def _resolve_country_and_city(
    country: Optional[str], city: Optional[str]
) -> Dict[str, int]:
    """Map country/city names to the filter ids the API expects."""
    filters: Dict[str, int] = {}
    countries = _get("servers/countries")

    country_obj = None
    if country:
        want = _norm(country)
        country_obj = next(
            (c for c in countries if _norm(c["name"]) == want or c["code"].lower() == want),
            None,
        )
        if country_obj is None:
            raise LookupError(f"unknown country: {country!r}")
        filters["filters[country_id]"] = country_obj["id"]

    if city:
        want = _norm(city)
        pool = [country_obj] if country_obj else countries
        match = None
        for c in pool:
            match = next((ci for ci in c["cities"] if _norm(ci["name"]) == want), None)
            if match:
                break
        if match is None:
            where = f" in {country!r}" if country else ""
            raise LookupError(f"unknown city: {city!r}{where}")
        filters["filters[country_city_id]"] = match["id"]

    return filters


def _resolve_technology(tech: str) -> int:
    identifier = _TECH_ALIASES.get(_norm(tech), _norm(tech))
    technologies = _get("technologies")
    match = next(
        (t for t in technologies
         if t["identifier"] == identifier or _norm(t["name"]) == _norm(tech)),
        None,
    )
    if match is None:
        raise LookupError(f"unknown technology: {tech!r}")
    return match["id"]


def _resolve_group(group: str) -> int:
    want = _norm(group)
    groups = _get("servers/groups")
    match = next(
        (g for g in groups
         if want in (_norm(g["title"]),
                     _norm(g["identifier"]),
                     _norm(g["identifier"].replace("legacy_", "")))),
        None,
    )
    if match is None:
        raise LookupError(f"unknown group: {group!r}")
    return match["id"]


def recommend(
    country: Optional[str] = None,
    city: Optional[str] = None,
    tech: Optional[str] = None,
    group: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, object]]:
    """Return up to ``limit`` recommended servers matching the given filters.

    ``limit=0`` returns every matching server. Any filter left as ``None`` is
    simply omitted, widening the search. Raises LookupError if a supplied name
    cannot be resolved.
    """
    filters: Dict[str, int] = {}
    filters.update(_resolve_country_and_city(country, city))
    if tech:
        filters["filters[servers_technologies]"] = _resolve_technology(tech)
    if group:
        filters["filters[servers_groups]"] = _resolve_group(group)

    pairs = list(filters.items()) + [("limit", limit)]
    # Keep the [] of the filter keys literal; the API expects them unencoded.
    query = "&".join(
        f"{urllib.parse.quote(str(k), safe='[]')}={urllib.parse.quote(str(v))}"
        for k, v in pairs
    )
    servers = _get("servers/recommendations", query)
    return [_summarise(s) for s in servers]


def server_record(server: str) -> Dict[str, object]:
    """Return the raw server record exactly as NordVPN's API returns it.

    ``server`` may be a short reference (e.g. "us9999") or a full hostname.
    Raises LookupError if no server with that hostname exists. Use this when you
    want everything the API exposes, including fields the helper does not curate.
    """
    host = hostname(server)
    server_id = _resolve_server_id(host)
    query = (
        f"filters{urllib.parse.quote('[servers.id]', safe='[]')}="
        f"{urllib.parse.quote(str(server_id))}&limit=1"
    )
    servers = _get("servers", query)
    if not isinstance(servers, list) or not servers:
        raise RuntimeError(f"server {host!r} (id {server_id}) disappeared from the API")
    return servers[0]


def server_metadata(server: str) -> Dict[str, object]:
    """Return a flattened, curated view of a server's metadata.

    ``server`` may be a short reference (e.g. "us9999") or a full hostname.
    Raises LookupError if no server with that hostname exists.
    """
    return _describe(server_record(server))


def _resolve_server_id(host: str) -> int:
    """Find a server's numeric id by hostname via a lightweight server listing.

    The /servers endpoint silently ignores a hostname filter, so we pull the
    full list trimmed to id + hostname (a few hundred KB instead of tens of MB,
    via the ``fields`` parameter) and match the hostname locally.
    """
    query = (
        "limit=0"
        f"&fields{urllib.parse.quote('[servers.id]', safe='[]')}"
        f"&fields{urllib.parse.quote('[servers.hostname]', safe='[]')}"
    )
    listing = _get("servers", query)
    if isinstance(listing, list):
        want = _norm(host)
        match = next(
            (s for s in listing if _norm(str(s.get("hostname", ""))) == want),
            None,
        )
        if match is not None:
            return int(match["id"])
    raise LookupError(f"no server found with hostname {host!r}; check the name")


def _tech_metadata(server: Dict[str, object], identifier: str, name: str) -> str:
    """Pull a named metadata value from one of a server's technologies."""
    for tech in server.get("technologies", []):
        if tech.get("identifier") == identifier:
            for entry in tech.get("metadata") or []:
                if entry.get("name") == name:
                    return str(entry.get("value", ""))
    return ""


def _technology_metadata(server: Dict[str, object]) -> Dict[str, Dict[str, str]]:
    """Collect every per-technology metadata entry as {identifier: {name: value}}.

    Captures whatever NordVPN exposes per technology — e.g. the WireGuard
    public_key, the proxy_hostname for proxy technologies, the NordWhisper port.
    """
    result: Dict[str, Dict[str, str]] = {}
    for tech in server.get("technologies", []):
        identifier = tech.get("identifier")
        entries = {
            entry["name"]: str(entry.get("value", ""))
            for entry in (tech.get("metadata") or [])
            if entry.get("name")
        }
        if identifier and entries:
            result[identifier] = entries
    return result


def _specifications(server: Dict[str, object]) -> Dict[str, object]:
    """Flatten the specifications block into {identifier: value}.

    A single value is unwrapped; multiple values are kept as a list (the API
    currently exposes just the server "version").
    """
    specs: Dict[str, object] = {}
    for spec in server.get("specifications", []):
        identifier = spec.get("identifier")
        values = [v.get("value") for v in spec.get("values", []) if v.get("value") is not None]
        if not identifier or not values:
            continue
        specs[identifier] = values[0] if len(values) == 1 else values
    return specs


def _describe(server: Dict[str, object]) -> Dict[str, object]:
    """Flatten a raw server record into a readable metadata mapping."""
    location = (server.get("locations") or [{}])[0]
    location = location if isinstance(location, dict) else {}
    country = location.get("country", {}) if isinstance(location, dict) else {}
    city = country.get("city", {}) if isinstance(country, dict) else {}
    return {
        "id": server.get("id", ""),
        "name": server.get("name", ""),
        "hostname": server.get("hostname", ""),
        "status": server.get("status", ""),
        "load": server.get("load"),
        "ip": server.get("station", ""),
        "ipv6": server.get("ipv6_station", ""),
        "country": country.get("name", ""),
        "country_code": country.get("code", ""),
        "city": city.get("name", ""),
        "latitude": location.get("latitude", ""),
        "longitude": location.get("longitude", ""),
        "dns_name": city.get("dns_name", ""),
        "hub_score": city.get("hub_score", ""),
        "created_at": server.get("created_at", ""),
        "updated_at": server.get("updated_at", ""),
        "specifications": _specifications(server),
        "technologies": sorted(
            t.get("identifier", "") for t in server.get("technologies", [])
            if t.get("identifier")
        ),
        # Per-technology metadata (proxy hostnames, NordWhisper port, ...).
        "technology_metadata": _technology_metadata(server),
        # The WireGuard server public key lives in the wireguard_udp technology's
        # metadata; paired with the NordLynx private key from `credentials` it is
        # enough to build a WireGuard config without connecting. Also present in
        # technology_metadata; surfaced here as a documented convenience.
        "wireguard_public_key": _tech_metadata(server, "wireguard_udp", "public_key"),
        "groups": [g.get("title", "") for g in server.get("groups", [])],
        "services": sorted(
            s.get("identifier", "") for s in server.get("services", [])
            if s.get("identifier")
        ),
    }


def embed_credentials(config: str, username: str, password: str) -> str:
    """Inline auth credentials into an .ovpn so it connects without prompting.

    Replaces the bare ``auth-user-pass`` directive with an inline block.
    """
    block = f"<auth-user-pass>\n{username}\n{password}\n</auth-user-pass>"
    lines, replaced = [], False
    for line in config.splitlines():
        if line.strip() == "auth-user-pass":
            lines.append(block)
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append(block)
    return "\n".join(lines) + "\n"


def root_ca(server: str) -> str:
    """Return NordVPN's root CA (PEM) extracted from a server's OpenVPN config."""
    config = openvpn_config(server, "udp")
    start, end = config.find("<ca>"), config.find("</ca>")
    if start == -1 or end == -1:
        raise LookupError("no <ca> block found in the OpenVPN config")
    return config[start + len("<ca>"):end].strip()


def ikev2_params(token: str, server: Optional[str] = None) -> Dict[str, object]:
    """Gather everything needed for a manual IKEv2/IPSec connection.

    No tunnel required: the server comes from the API (or the caller), the
    username/password from the token, and the CA from a server's OpenVPN config.
    """
    if server:
        host = hostname(server)
    else:
        recommended = recommend(tech="ikev2", limit=1)
        if not recommended:
            raise RuntimeError("no IKEv2-capable server is currently available")
        host = str(recommended[0]["hostname"])

    try:
        ip = socket.gethostbyname(host)
    except OSError:
        ip = ""

    creds = service_credentials(token)

    try:
        ca = root_ca(host)
    except (LookupError, RuntimeError):
        # The CA is identical across servers; fall back to a recommended one if
        # this host has no downloadable OpenVPN config.
        fallback = recommend(limit=1)
        ca = root_ca(str(fallback[0]["hostname"])) if fallback else ""

    return {
        "vpn_type": "IKEv2/IPSec",
        "server": host,
        "ip": ip,
        "remote_id": host,
        "username": creds["username"],
        "password": creds["password"],
        "ca": ca,
    }


def service_credentials(token: str) -> Dict[str, object]:
    """Fetch the account's NordVPN service credentials using the login token.

    These are the username/password for *manual* OpenVPN/IKEv2 setup, plus the
    NordLynx (WireGuard) private key — all derived from the same token the
    daemon logs in with.
    """
    if not token:
        raise RuntimeError("no credentials provided: set NORDVPN_TOKEN")
    data = _get("users/services/credentials", token=token)
    if not isinstance(data, dict):
        raise RuntimeError("unexpected response from the credentials endpoint")
    return {
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "nordlynx_private_key": data.get("nordlynx_private_key", ""),
    }


def _summarise(server: Dict[str, object]) -> Dict[str, object]:
    location = (server.get("locations") or [{}])[0]
    country = location.get("country", {}) if isinstance(location, dict) else {}
    city = country.get("city", {}) if isinstance(country, dict) else {}
    return {
        "hostname": server.get("hostname", ""),
        "load": server.get("load"),
        "ip": server.get("station", ""),
        "city": city.get("name", ""),
        "country": country.get("name", ""),
        "groups": [g.get("title", "") for g in server.get("groups", [])],
    }
