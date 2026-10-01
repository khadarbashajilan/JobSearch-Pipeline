"""Allow running as a module: python -m jobsearch."""

from jobsearch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())