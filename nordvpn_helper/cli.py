"""Command dispatch and exit-code handling for nordvpn-helper."""

from __future__ import annotations

import json
import os
import shlex
import sys
from typing import Callable, Dict, List, Optional

from . import api
from . import nord
from . import wireguard
from .proc import CommandError, log

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2

PROG = "nordvpn-helper"

HELP_TEXT = f"""\
{PROG} — non-interactive helpers around the NordVPN Linux client.

Usage:
  {PROG} <command> [args]

Commands:
  help                 Show this message.
  shell                Start an interactive REPL: run commands line by line in a
                       single process (and, in the container, against one
                       already-started daemon) instead of paying startup per
                       command. Quit with `exit`, `quit`, or Ctrl-D.
  wireguard-info [target]
                       Connect via NordLynx and print the WireGuard config.
                       target (optional) is passed to `nordvpn connect`: a
                       country, city, server, or group. Default: recommended.
  countries            List the countries NordVPN has servers in.
  cities <country>     List the cities with servers in a country.
  groups               List the server groups (P2P, Double_VPN, regions, ...).
  technologies [--source api|cli]
                       List VPN technologies. api (default) = HTTP API
                       identifiers; cli = nordlynx/openvpn for the daemon.
  account              Show the logged-in account (email, subscription).
  credentials          Show the service credentials (manual OpenVPN/IKEv2
                       username & password, NordLynx private key) for the token.
  recommend [filters]  List recommended servers. Filters (all optional):
                         --source api|cli  api (default): HTTP API, lists many;
                                           cli: connect via daemon, report one
                         --country <name>  --city <name>
                         --tech <name>     --group <name>
                         --limit <n>       (api only; default 5, 0 = all)
  openvpn-config <server> [--protocol udp|tcp] [--with-credentials]
                       Print the OpenVPN (.ovpn) config for a server (e.g.
                       us9999). Default protocol: udp. --with-credentials inlines
                       the service username/password (self-contained, secret).
  ikev2-info [server]  Print IKEv2/IPSec parameters (server, username, password,
                       CA) for manual setup. Default: a recommended IKEv2 server.
  server-info <server> [--raw]
                       Show all metadata for a server (location, load, status,
                       version, technologies and their metadata — WireGuard
                       public key, proxy hostnames, NordWhisper port — groups,
                       services) from the HTTP API. server is a short reference
                       or hostname (e.g. us9999). --raw dumps the API's server
                       record verbatim (JSON), exposing any uncurated fields.

Environment variables:
  NORDVPN_TOKEN          NordVPN login token (required; the only non-interactive
                         login method the client supports).
  NORDVPN_INTERFACE      WireGuard interface to read (default: nordlynx).
                         Known NordVPN interfaces:
                           nordlynx  NordLynx / WireGuard (used by wireguard-info)
                           nordtun   OpenVPN (not readable by wireguard-info)
  OUTPUT_FORMAT          Output format for all commands: text (default) or json.

Exit codes:
  0  success
  1  runtime error (auth, network, daemon not ready, ...)
  2  usage error (unknown command, bad arguments)
"""

# Technologies the `nordvpn` CLI can connect with (`nordvpn set technology`).
# The API has a longer list (see `technologies --source api`); these are the
# ones meaningful to the daemon-driven `recommend --source cli` / wireguard-info.
CLI_TECHNOLOGIES = ["nordlynx", "openvpn"]

# Map either vocabulary (CLI name or API identifier) onto a `set technology`
# value, so `--tech nordlynx` and `--tech wireguard_udp` both work for the CLI.
_CLI_TECH = {
    "nordlynx": "nordlynx",
    "wireguard": "nordlynx",
    "wireguard_udp": "nordlynx",
    "openvpn": "openvpn",
    "openvpn_udp": "openvpn",
    "openvpn_tcp": "openvpn",
}


class UsageError(Exception):
    """A command was invoked with missing or invalid arguments."""


def _output_format() -> str:
    """Resolve the desired output format from OUTPUT_FORMAT (default: text)."""
    value = os.environ.get("OUTPUT_FORMAT", "").strip().lower()
    if not value:
        return "text"
    if value in ("text", "json"):
        return value
    log(f"unknown OUTPUT_FORMAT '{value}', falling back to text")
    return "text"


def _emit_list(items: List[str], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(items, indent=2))
    else:
        print("\n".join(items))


def _emit_key_values(raw: str, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(nord.parse_key_values(raw), indent=2))
    else:
        print(raw)


def cmd_help(_args: List[str]) -> int:
    print(HELP_TEXT, end="")
    return EXIT_OK


def cmd_shell(_args: List[str]) -> int:
    """Read commands line by line and dispatch each in this one process.

    Avoids per-command process/container/daemon startup: in the container the
    entrypoint starts nordvpnd once before dropping into this loop. Reads until
    EOF (Ctrl-D) or an `exit`/`quit` line; a failing command logs its error and
    the loop continues. The prompt goes to stderr so stdout stays pure command
    output (configs, JSON) for redirection.
    """
    log("interactive shell — type 'help' for commands; 'exit', 'quit' or Ctrl-D to leave")
    while True:
        try:
            print(f"{PROG}> ", end="", file=sys.stderr, flush=True)
            line = input()
        except EOFError:
            print(file=sys.stderr)  # newline after a Ctrl-D at the prompt
            break
        except KeyboardInterrupt:
            print(file=sys.stderr)  # Ctrl-C abandons the line, keeps the shell
            continue

        try:
            argv = shlex.split(line)
        except ValueError as exc:
            log(f"parse error: {exc}")
            continue
        if not argv:
            continue
        if argv[0] in ("exit", "quit"):
            break
        if argv[0] == "shell":
            log("already in a shell")
            continue
        dispatch(argv)
    return EXIT_OK


def cmd_wireguard_info(args: List[str]) -> int:
    interface = os.environ.get("NORDVPN_INTERFACE", "nordlynx").strip() or "nordlynx"
    fmt = _output_format()

    nord.login()
    nord.set_technology("nordlynx")
    nord.connect(args)
    try:
        nord.wait_until_connected(interface)
        data = wireguard.collect(interface, status=nord.status())
        output = wireguard.format_json(data) if fmt == "json" else wireguard.format_text(data)
        print(output)
    finally:
        nord.disconnect()
    return EXIT_OK


def cmd_countries(_args: List[str]) -> int:
    _emit_list(api.countries(), _output_format())
    return EXIT_OK


def cmd_cities(args: List[str]) -> int:
    if not args:
        raise UsageError("cities requires a country, e.g. `cities United_States`")
    try:
        result = api.cities(args[0])
    except LookupError as exc:
        raise UsageError(str(exc))
    _emit_list(result, _output_format())
    return EXIT_OK


def cmd_groups(_args: List[str]) -> int:
    _emit_list(api.groups(), _output_format())
    return EXIT_OK


def _parse_flags(args: List[str], flags: Dict[str, str]) -> Dict[str, str]:
    """Parse `--flag value` pairs into a dict, per the given flag->key map."""
    parsed: Dict[str, str] = {}
    i = 0
    while i < len(args):
        flag = args[i]
        key = flags.get(flag)
        if key is None:
            raise UsageError(f"unknown option {flag!r}; valid: {' '.join(flags)}")
        if i + 1 >= len(args):
            raise UsageError(f"{flag} requires a value")
        parsed[key] = args[i + 1]
        i += 2
    return parsed


def _source(opts: Dict[str, str]) -> str:
    source = (opts.get("source") or "api").lower()
    if source not in ("api", "cli"):
        raise UsageError(f"--source must be 'api' or 'cli', got {opts.get('source')!r}")
    return source


def cmd_technologies(args: List[str]) -> int:
    source = _source(_parse_flags(args, {"--source": "source"}))
    techs = api.technologies() if source == "api" else sorted(CLI_TECHNOLOGIES)
    _emit_list(techs, _output_format())
    return EXIT_OK


def cmd_account(_args: List[str]) -> int:
    _emit_key_values(nord.run_cli(["account"], login_required=True),
                     _output_format())
    return EXIT_OK


def cmd_credentials(_args: List[str]) -> int:
    token = os.environ.get("NORDVPN_TOKEN", "").strip()
    creds = api.service_credentials(token)
    if _output_format() == "json":
        print(json.dumps(creds, indent=2))
    else:
        for key, value in creds.items():
            print(f"{key} = {value}")
    return EXIT_OK


def cmd_openvpn_config(args: List[str]) -> int:
    server: Optional[str] = None
    protocol = "udp"
    with_credentials = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--protocol":
            if i + 1 >= len(args):
                raise UsageError("--protocol requires a value (udp or tcp)")
            protocol = args[i + 1].strip().lower()
            i += 2
        elif arg == "--with-credentials":
            with_credentials = True
            i += 1
        elif arg.startswith("-"):
            raise UsageError(f"unknown option {arg!r}; valid: --protocol --with-credentials")
        elif server is None:
            server = arg
            i += 1
        else:
            raise UsageError(f"unexpected argument {arg!r}")

    if not server:
        raise UsageError("openvpn-config requires a server, e.g. `openvpn-config us9999`")
    if protocol not in ("udp", "tcp"):
        raise UsageError(f"--protocol must be 'udp' or 'tcp', got {protocol!r}")

    try:
        config = api.openvpn_config(server, protocol)
    except LookupError as exc:
        raise UsageError(str(exc))

    if with_credentials:
        creds = api.service_credentials(os.environ.get("NORDVPN_TOKEN", "").strip())
        config = api.embed_credentials(config, creds["username"], creds["password"])

    if _output_format() == "json":
        print(json.dumps(
            {"server": api.hostname(server), "protocol": protocol, "config": config},
            indent=2))
    else:
        print(config, end="")
    return EXIT_OK


def cmd_server_info(args: List[str]) -> int:
    server: Optional[str] = None
    raw = False
    for arg in args:
        if arg == "--raw":
            raw = True
        elif arg.startswith("-"):
            raise UsageError(f"unknown option {arg!r}; valid: --raw")
        elif server is None:
            server = arg
        else:
            raise UsageError(f"unexpected argument {arg!r}")

    if not server:
        raise UsageError("server-info requires a server, e.g. `server-info us9999`")

    try:
        # --raw dumps the API's server record verbatim (always JSON, since the
        # record is a nested object) so fields the helper doesn't curate stay
        # available; otherwise emit the flattened, curated view.
        if raw:
            print(json.dumps(api.server_record(server), indent=2))
            return EXIT_OK
        meta = api.server_metadata(server)
    except LookupError as exc:
        raise UsageError(str(exc))

    if _output_format() == "json":
        print(json.dumps(meta, indent=2))
    else:
        for key in ("id", "name", "hostname", "status", "load", "ip", "ipv6",
                    "country", "country_code", "city", "latitude", "longitude",
                    "dns_name", "hub_score", "created_at", "updated_at"):
            value = meta[key]
            if value not in ("", None):
                suffix = "%" if key == "load" else ""
                print(f"{key} = {value}{suffix}")
        for identifier, value in meta["specifications"].items():
            rendered = ", ".join(value) if isinstance(value, list) else value
            print(f"{identifier} = {rendered}")
        if meta["technologies"]:
            print(f"technologies = {', '.join(meta['technologies'])}")
        if meta["wireguard_public_key"]:
            print(f"wireguard_public_key = {meta['wireguard_public_key']}")
        for tech in sorted(meta["technology_metadata"]):
            for name, value in meta["technology_metadata"][tech].items():
                # Shown above as the dedicated wireguard_public_key line.
                if tech == "wireguard_udp" and name == "public_key":
                    continue
                print(f"{tech}.{name} = {value}")
        if meta["groups"]:
            print(f"groups = {', '.join(meta['groups'])}")
        if meta["services"]:
            print(f"services = {', '.join(meta['services'])}")
    return EXIT_OK


def cmd_ikev2_info(args: List[str]) -> int:
    server: Optional[str] = None
    for arg in args:
        if arg.startswith("-"):
            raise UsageError(f"unknown option {arg!r}")
        if server is not None:
            raise UsageError(f"unexpected argument {arg!r}")
        server = arg

    token = os.environ.get("NORDVPN_TOKEN", "").strip()
    try:
        params = api.ikev2_params(token, server)
    except LookupError as exc:
        raise UsageError(str(exc))

    if _output_format() == "json":
        print(json.dumps(params, indent=2))
    else:
        for key in ("vpn_type", "server", "ip", "remote_id", "username", "password"):
            print(f"{key} = {params[key]}")
        if params["ca"]:
            print("ca =")
            print(params["ca"])
    return EXIT_OK


_RECOMMEND_FLAGS = {
    "--source": "source",
    "--country": "country",
    "--city": "city",
    "--tech": "tech",
    "--group": "group",
    "--limit": "limit",
}


def cmd_recommend(args: List[str]) -> int:
    opts = _parse_flags(args, _RECOMMEND_FLAGS)
    servers = (_recommend_cli if _source(opts) == "cli" else _recommend_api)(opts)
    _emit_servers(servers, _output_format())
    return EXIT_OK


def _recommend_api(opts: Dict[str, str]) -> List[Dict[str, object]]:
    """Variant 1: list servers from NordVPN's HTTP API (API tech identifiers)."""
    limit_raw = opts.get("limit", "5")
    try:
        limit = int(limit_raw)
        if limit < 0:
            raise ValueError
    except ValueError:
        raise UsageError(
            f"--limit must be a non-negative integer (0 = all), got {limit_raw!r}")
    try:
        return api.recommend(
            country=opts.get("country"),
            city=opts.get("city"),
            tech=opts.get("tech"),
            group=opts.get("group"),
            limit=limit,
        )
    except LookupError as exc:
        raise UsageError(str(exc))


def _recommend_cli(opts: Dict[str, str]) -> List[Dict[str, object]]:
    """Variant 2: ask the nordvpn daemon by connecting, then reading status.

    The CLI cannot list servers, so this connects to the (optionally filtered)
    recommended server, reports it, and disconnects. Uses CLI technologies
    (nordlynx/openvpn) via `nordvpn set technology`.
    """
    if opts.get("limit") not in (None, "1"):
        log("--source cli connects to a single server; --limit is ignored")

    cli_tech = None
    if opts.get("tech"):
        cli_tech = _CLI_TECH.get(opts["tech"].strip().lower().replace("-", "_"))
        if cli_tech is None:
            raise UsageError("--source cli supports --tech "
                             f"{'/'.join(CLI_TECHNOLOGIES)}, got {opts['tech']!r}")

    target: List[str] = []
    if opts.get("group"):
        target += ["--group", opts["group"]]
    if opts.get("country"):
        target.append(opts["country"])
    if opts.get("city"):
        target.append(opts["city"])

    nord.login()
    if cli_tech:
        nord.set_technology(cli_tech)
    nord.connect(target)
    try:
        status = nord.status()
    finally:
        nord.disconnect()
    return [{
        "hostname": status.get("hostname", ""),
        "load": "",
        "ip": status.get("ip", ""),
        "city": status.get("city", ""),
        "country": status.get("country", ""),
        "groups": [],
    }]


def _emit_servers(servers: List[Dict[str, object]], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(servers, indent=2))
        return
    if not servers:
        log("no servers matched the given filters")
    for s in servers:
        load = f"load={s['load']}%  " if s.get("load") not in ("", None) else ""
        groups = ", ".join(s["groups"])
        extra = f"  [{groups}]" if groups else ""
        print(f"{s['hostname']}  {load}{s['ip']}  {s['city']}, {s['country']}{extra}")


COMMANDS: Dict[str, Callable[[List[str]], int]] = {
    "help": cmd_help,
    "--help": cmd_help,
    "-h": cmd_help,
    "shell": cmd_shell,
    "wireguard-info": cmd_wireguard_info,
    "countries": cmd_countries,
    "cities": cmd_cities,
    "groups": cmd_groups,
    "technologies": cmd_technologies,
    "account": cmd_account,
    "credentials": cmd_credentials,
    "recommend": cmd_recommend,
    "openvpn-config": cmd_openvpn_config,
    "ikev2-info": cmd_ikev2_info,
    "server-info": cmd_server_info,
}


def dispatch(argv: List[str]) -> int:
    """Run a single command (argv[0] + args), mapping errors to exit codes.

    Shared by one-shot invocation (`main`) and the interactive `shell` loop.
    """
    command, args = argv[0], argv[1:]
    handler = COMMANDS.get(command)
    if handler is None:
        log(f"unknown command: {command!r}")
        print(HELP_TEXT, end="")
        return EXIT_USAGE

    try:
        return handler(args)
    except UsageError as exc:
        log(f"usage error: {exc}")
        return EXIT_USAGE
    except (RuntimeError, CommandError) as exc:
        log(f"error: {exc}")
        return EXIT_RUNTIME
    except KeyboardInterrupt:
        log("interrupted")
        return EXIT_RUNTIME


def main(argv: List[str]) -> int:
    if not argv:
        return cmd_help([])
    return dispatch(argv)
