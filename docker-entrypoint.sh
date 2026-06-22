#!/bin/bash
#
# Container entrypoint: dispatch to the nordvpn_helper Python app. For commands
# that need the VPN, start the nordvpnd daemon in the background and wait for it
# to be ready first. `help` (and no command) run immediately without the daemon.
# `shell` (the interactive REPL) is intentionally not in the no-daemon list: it
# falls through to the daemon-start path so the REPL runs against one already
# started nordvpnd. Run it with `docker run -it ... shell`.
#
set -eu

# Run as a module so the package's relative imports resolve. WORKDIR is /opt
# (set in the Dockerfile), which puts the nordvpn_helper package on sys.path.
HELPER=(python3 -m nordvpn_helper)

# The first argument is the subcommand. Default to "help" (matches Dockerfile CMD).
COMMAND="${1:-help}"

# Commands that don't need a running daemon: serve them straight away. These all
# hit NordVPN's HTTP API (or a static download) rather than the local daemon, so
# they need neither a login nor a tunnel. recommend only needs the daemon for its
# CLI variant (`--source cli`), which connects via the daemon (handled below).
case "${COMMAND}" in
    help | -h | --help | "" | technologies | credentials | openvpn-config | ikev2-info \
        | countries | cities | groups | server-info)
        exec "${HELPER[@]}" "$@"
        ;;
    recommend)
        case " $* " in
            *" --source cli "* | *" --source=cli "*) ;;  # needs the daemon
            *) exec "${HELPER[@]}" "$@" ;;               # API variant: no daemon
        esac
        ;;
esac

# State directories the daemon expects.
mkdir -p /var/lib/nordvpn /run/nordvpn

# Start nordvpnd if it isn't already running.
if ! pgrep -x nordvpnd > /dev/null 2>&1; then
    echo "[entrypoint] starting nordvpnd" >&2
    nohup nordvpnd > /var/log/nordvpnd.log 2>&1 &
    NORDVPND_PID=$!
    # Detach: don't let a SIGPIPE from nordvpnd propagate to the entrypoint.
    disown "${NORDVPND_PID}" 2>/dev/null || true
fi

# Wait up to 30s for the daemon to accept CLI commands.
echo "[entrypoint] waiting for nordvpnd" >&2
for _ in $(seq 1 30); do
    if nordvpn status > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ! nordvpn status > /dev/null 2>&1; then
    echo "[entrypoint] nordvpnd did not become ready in time" >&2
    echo "[entrypoint] --- last lines of /var/log/nordvpnd.log ---" >&2
    tail -n 20 /var/log/nordvpnd.log >&2 2>/dev/null || true
    exit 1
fi

echo "[entrypoint] nordvpnd ready; running: ${COMMAND}" >&2
exec "${HELPER[@]}" "$@"
