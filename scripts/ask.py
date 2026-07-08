"""M5 CLI: ask a cited question over the indexed filings.

Usage:
    python -m scripts.ask "What risks does Apple disclose about supply chain?"
"""

import argparse

import structlog
from dotenv import load_dotenv

load_dotenv()

from agents.graph import ask  # noqa: E402
from agents.llm import OpenAIChat  # noqa: E402
from retrieval.embeddings import OpenAIEmbedder  # noqa: E402

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a question over indexed SEC filings.")
    parser.add_argument("question")
    args = parser.parse_args()

    result = ask(args.question, OpenAIChat(), OpenAIEmbedder())

    print(f"\n{'=' * 70}\nANSWER{' (REFUSED)' if result.refused else ''}:\n{result.answer}\n")
    if result.citations:
        print("SOURCES:")
        for c in result.citations:
            h = c.hit
            print(f"  [{c.label}] {h.company_name} {h.form_type} FY{h.fiscal_year} — {h.section} ({h.filing_accession})")


if __name__ == "__main__":
    main()
