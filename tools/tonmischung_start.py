"""Startpunkt im Paket (PyInstaller): ohne Argumente die App, sonst wie `python3 -m tonmischung …`."""
import sys

from tonmischung.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["app"]))
