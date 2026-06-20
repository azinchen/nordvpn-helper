"""Allow running the package directly: `python3 /opt/nordvpn_helper` / `-m nordvpn_helper`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
