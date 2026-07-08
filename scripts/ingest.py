"""M1 CLI entrypoint: ingest one company's recent filings end-to-end.

Usage:
    python -m scripts.ingest --cik 0000320193 --limit 5

Find a CIK at https://www.sec.gov/cgi-bin/browse-edgar, or use a well-known
one for testing (Apple = 320193, Microsoft = 789019, JPMorgan = 19617).
"""

import argparse
import asyncio

import structlog
from dotenv import load_dotenv

load_dotenv()

from ingestion.pipeline import ingest_company  # noqa: E402  (must follow load_dotenv)

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
log = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a company's recent SEC filings into Ledger Lens.")
    parser.add_argument("--cik", required=True, help="SEC CIK number, with or without leading zeros")
    parser.add_argument("--limit", type=int, default=5, help="Max filings to ingest this run")
    args = parser.parse_args()

    summary = asyncio.run(ingest_company(args.cik, limit=args.limit))
    log.info("done", **summary)


if __name__ == "__main__":
    main()
