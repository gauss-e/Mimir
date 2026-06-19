"""Entry point: the ``mimir`` console script."""

from __future__ import annotations

import argparse

from app import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="mimir", description="Mimir career advisor")
    parser.add_argument("-c", "--config", help="path to config.toml", default=None)
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    main()
