# syntax=docker/dockerfile:1.7
#
# nordvpn-helper: non-interactive Docker wrapper for the NordVPN Linux client.
# Builds an Ubuntu-based image with the official nordvpn client and a small
# Python entrypoint that dispatches subcommands.
#

FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Base packages: prerequisites for adding the NordVPN apt repo + tools we need
# at runtime to read the wireguard interface and address info.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        iproute2 \
        iptables \
        nftables \
        wireguard-tools \
        python3 \
        python3-minimal \
    && rm -rf /var/lib/apt/lists/*

# Install the official NordVPN Linux client from its apt repository.
#
# We add the repo manually (rather than running the upstream install.sh, which
# is interactive and tries to enable a systemd service) so the build stays
# unattended and reproducible:
#   1. fetch and store NordVPN's signing key under /etc/apt/keyrings,
#   2. register the repo pinned to that key via `signed-by`,
#   3. install the `nordvpn` package with -y.
RUN set -eux; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://repo.nordvpn.com/gpg/nordvpn_public.asc \
        -o /etc/apt/keyrings/nordvpn_public.asc; \
    echo "deb [signed-by=/etc/apt/keyrings/nordvpn_public.asc] https://repo.nordvpn.com/deb/nordvpn/debian stable main" \
        > /etc/apt/sources.list.d/nordvpn.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends nordvpn; \
    rm -rf /var/lib/apt/lists/*; \
    command -v nordvpn; \
    command -v nordvpnd

# Application code.
COPY nordvpn_helper /opt/nordvpn_helper

# Entrypoint starts the nordvpnd daemon and waits for it before running the
# user-requested subcommand.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Runtime state for the NordVPN client (login token, settings, keys).
# Mount a named volume here to persist login across runs.
VOLUME ["/var/lib/nordvpn"]

WORKDIR /opt

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["help"]
