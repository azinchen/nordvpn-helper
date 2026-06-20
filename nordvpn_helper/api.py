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
