# NordVPN Helper

> Pull NordVPN connection configs and info from a token — non-interactively, in Docker.

A non-interactive Docker wrapper around the [NordVPN Linux client](https://nordvpn.com/download/linux/). From just a login token it extracts connection configs and account info — WireGuard/NordLynx, OpenVPN, and IKEv2 setups, service credentials, and server discovery — with structured (text or JSON) output and no TTY.

## Why

The official NordVPN client is interactive and assumes a systemd-managed host. This image packages it for one-shot, scripted use — every parameter is an env var or CLI argument, output is structured, and no TTY is required.

## Image

Prebuilt images are published to Docker Hub and the GitHub Container Registry:

```bash
docker pull azinchen/nordvpn-helper           # Docker Hub
docker pull ghcr.io/azinchen/nordvpn-helper   # GitHub Container Registry
```

Or build it yourself:

```bash
docker build -t azinchen/nordvpn-helper .
```

## Quick start

Set your token and define a reusable alias for the published image. The
capabilities, token, and volume are harmless for commands that don't use them:

```bash
export NORDVPN_TOKEN=your-long-token-here

alias nordvpn-helper='docker run --rm -e NORDVPN_TOKEN -e OUTPUT_FORMAT \
    --cap-add=NET_ADMIN --device /dev/net/tun \
    -v nordvpn-data:/var/lib/nordvpn azinchen/nordvpn-helper'

nordvpn-helper help
nordvpn-helper wireguard-info                        # recommended server
nordvpn-helper wireguard-info United_States Chicago  # a specific location
nordvpn-helper wireguard-info us9999                 # a specific server

# JSON instead of a config file:
OUTPUT_FORMAT=json nordvpn-helper wireguard-info
```

The API-only commands (`recommend`, `openvpn-config`, `technologies`,
`credentials`) need neither `--cap-add=NET_ADMIN` nor `--device /dev/net/tun` —
drop them if that's all you run.

## Commands

| Command | What it does |
|---|---|
| `help` | Show usage and env vars |
| `wireguard-info [target]` | Connect with NordLynx and print the WireGuard configuration. `target` is passed to `nordvpn connect` — a country, city, server, or group (e.g. `United_States Chicago`, `us9999`, `P2P`). Default: the recommended server |
| `countries` | List the countries NordVPN has servers in |
| `cities <country>` | List the cities with servers in a country (e.g. `cities United_States`) |
| `groups` | List the server groups (P2P, Double_VPN, regions, …) |
| `technologies [--source api\|cli]` | List VPN technologies. `api` (default) = HTTP API identifiers (`wireguard_udp`, `openvpn_udp`, …); `cli` = the daemon's connectable set (`nordlynx`, `openvpn`) |
| `account` | Show the logged-in account (email, subscription) |
| `credentials` | Show the service credentials derived from the token: manual OpenVPN/IKEv2 username & password, plus the NordLynx private key |
| `recommend [filters]` | List recommended servers. `--source api` (default) queries the HTTP API and lists many servers with load; `--source cli` connects via the daemon and reports the one server it picks. Filters (all optional): `--country`, `--city`, `--tech`, `--group`, `--limit` (api only; default 5, `0` = all) |
| `openvpn-config <server> [--protocol udp\|tcp] [--with-credentials]` | Print the OpenVPN (`.ovpn`) config for a specific server (e.g. `us9999`). Default protocol: `udp`. `--with-credentials` inlines the service username/password so the config is self-contained (⚠️ contains a secret) |
| `ikev2-info [server]` | Print IKEv2/IPSec parameters for manual setup — server address, remote ID, service username/password, and NordVPN's root CA. No connection needed. Default: a recommended IKEv2 server |

Every command prints text by default, or JSON with `OUTPUT_FORMAT=json`.

- `wireguard-info` and `recommend --source cli` establish a tunnel, so they need `NET_ADMIN` + `/dev/net/tun`.
- `account`, `countries`, `cities`, `groups` query the daemon (login, but no tunnel).
- `help`, `credentials`, `technologies`, `openvpn-config`, `ikev2-info`, and `recommend` (API variant) need neither the daemon nor capabilities — they hit the HTTP API or a static download (`technologies --source cli` is a static list).

Using the `nordvpn-helper` alias from [Quick start](#quick-start):

```bash
nordvpn-helper countries
OUTPUT_FORMAT=json nordvpn-helper groups
nordvpn-helper cities United_States

# Recommended servers — omit any filter to widen the search:
nordvpn-helper recommend --country United_States --city Chicago --tech nordlynx --group P2P --limit 3
nordvpn-helper recommend --tech nordlynx --limit 5

# Same query against the daemon instead of the HTTP API (returns one server):
nordvpn-helper recommend --source cli --country United_States --tech nordlynx

# OpenVPN config for a specific server (save it to a file):
nordvpn-helper openvpn-config us9999 > us9999.ovpn
nordvpn-helper openvpn-config us9999 --protocol tcp > us9999.tcp.ovpn

# Self-contained .ovpn with credentials inlined (ready for `openvpn --config`):
nordvpn-helper openvpn-config us9999 --with-credentials > us9999.ovpn

# IKEv2/IPSec parameters for manual setup (no connection needed):
nordvpn-helper ikev2-info              # a recommended IKEv2 server
nordvpn-helper ikev2-info de718        # a specific server
```

### Which `--tech` to use with `recommend`

`nordlynx` (WireGuard) and `openvpn` work with **both** sources, so when in doubt use those:

| You want | `--source api` (default) | `--source cli` |
|---|---|---|
| WireGuard / NordLynx | `nordlynx` or `wireguard_udp` | `nordlynx` (or `wireguard_udp`) |
| OpenVPN | `openvpn`, `openvpn_udp`, `openvpn_tcp` | `openvpn` (or `openvpn_udp`/`openvpn_tcp`) |
| Anything else | any identifier from `technologies --source api` (e.g. `ikev2`, `nordwhisper`) | not available — the daemon only connects with `nordlynx`/`openvpn` |

- The **api** variant accepts the friendly names (`nordlynx`, `openvpn`) **and** any identifier listed by `technologies --source api`.
- The **cli** variant accepts only what the daemon can connect with — `nordlynx` or `openvpn` — but also understands the matching API identifiers (`wireguard_udp` → `nordlynx`, `openvpn_udp`/`openvpn_tcp` → `openvpn`). Passing an api-only tech like `ikev2` is a usage error.
- Run `technologies --source api` or `technologies --source cli` to see the valid values for each.

## Environment variables

| Variable | Purpose |
|---|---|
| `NORDVPN_TOKEN` | NordVPN login token — the only supported login method (required) |
| `NORDVPN_INTERFACE` | Interface to read (default: `nordlynx`). NordVPN interfaces: `nordlynx` (NordLynx/WireGuard, used by `wireguard-info`), `nordtun` (OpenVPN, not readable by `wireguard-info`) |
| `OUTPUT_FORMAT` | `text` (default) or `json`, for all commands |

## Exit codes

- `0` — success
- `1` — runtime error (auth, network, daemon not ready, etc.)
- `2` — usage error (unknown command, bad args)

## How it works

The image installs the official NordVPN Linux client straight from NordVPN's apt repository (the repo and signing key are added in the Dockerfile — no interactive install script). Commands are dispatched by a small Python package, `nordvpn_helper`, run as `python3 -m nordvpn_helper <command>`.

For `help`, the entrypoint runs immediately. For commands that need the VPN (`wireguard-info`), it first starts `nordvpnd` (the daemon) in the background and waits up to 30 seconds for it to be ready, then execs the requested subcommand. Persistent state (login, settings, keys) lives in `/var/lib/nordvpn` — mount a named volume there to keep it across runs.

## Runtime requirements

The container needs the network capabilities to actually establish a tunnel:

```bash
docker run --rm \
    --cap-add=NET_ADMIN \
    --device /dev/net/tun \
    -e NORDVPN_TOKEN=... \
    -v nordvpn-data:/var/lib/nordvpn \
    azinchen/nordvpn-helper wireguard-info
```

`NET_ADMIN` lets the container configure routes and nftables. `/dev/net/tun` is the tunnel device WireGuard runs on.

## Limitations

- `recommend` resolves names via NordVPN's public HTTP API (`api.nordvpn.com`);
  the other server lists come from the `nordvpn` CLI, which can't enumerate
  individual servers.
- `nordvpnd` startup adds a few seconds of overhead per command, except the ones
  that skip the daemon entirely (`help`, `technologies`, `credentials`,
  `openvpn-config`, `ikev2-info`, and `recommend` without `--source cli`).
- Login is token-only. The current NordVPN client removed username/password
  login, and its default flow opens a browser — neither works headless, so
  `NORDVPN_TOKEN` is required.
