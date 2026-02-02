#!/usr/bin/env python3
"""Entry point shim — delegates to the migrate package.

Usage unchanged:
    python3 scripts/migrate.py <path-to-maven-project> [--output <dir>] [--dry-run]
    python3 scripts/migrate.py <path-to-maven-project> --mode overlay [--dry-run]
"""
from migrate.cli import main

if __name__ == "__main__":
    main()
