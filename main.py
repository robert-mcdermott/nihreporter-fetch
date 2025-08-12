from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from nihreporter_client import ReporterClient, Endpoint


def load_json_input(criteria_file: Optional[str], criteria_json: Optional[str]) -> Dict[str, Any]:
    if criteria_file and criteria_json:
        raise ValueError("Provide either --criteria-file or --criteria-json, not both.")
    if criteria_file:
        p = Path(criteria_file)
        if not p.exists():
            raise FileNotFoundError(f"Criteria file not found: {criteria_file}")
        return json.loads(p.read_text())
    if criteria_json:
        return json.loads(criteria_json)
    # default empty criteria
    return {}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NIH RePORTER fetch CLI: query projects or publications and save results to JSON."
    )
    parser.add_argument(
        "--endpoint",
        choices=["projects", "publications"],
        default="projects",
        help="Which API to query (default: projects)",
    )
    parser.add_argument(
        "--criteria-file",
        help="Path to JSON file containing the full POST body (e.g., {'criteria': {...}, 'include_fields': [...], 'limit': 500}).",
    )
    parser.add_argument(
        "--criteria-json",
        help="Inline JSON string for the full POST body. Mutually exclusive with --criteria-file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="output.json",
        help="Output JSON file path (default: output.json)",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=500,
        help="Records per page (max 500). Overrides 'limit' in the criteria body (default: 500)",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Optional cap on total records to fetch (default: no cap)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="Requests per second (recommended <= 1.0). Default 1.0",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout per request in seconds (default: 60)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retry attempts for transient errors (default: 3)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging to stderr.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    try:
        body = load_json_input(args.criteria_file, args.criteria_json)
    except Exception as e:
        print(f"Error loading criteria: {e}", file=sys.stderr)
        return 2

    endpoint: Endpoint = Endpoint.PROJECTS if args.endpoint == "projects" else Endpoint.PUBLICATIONS

    client = ReporterClient(
        endpoint=endpoint,
        page_limit=args.page_limit,
        max_records=args.max_records,
        rate_per_sec=args.rate,
        timeout=args.timeout,
        retries=args.retries,
        verbose=args.verbose,
    )

    if "limit" in body:
        # We'll enforce page size via client.page_limit to avoid user accidentally exceeding 500
        body = {**body, "limit": min(int(body["limit"]), args.page_limit)}
    else:
        body = {**body, "limit": args.page_limit}

    try:
        results, meta = client.fetch_all(body)
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.write_text(json.dumps({"meta": meta, "results": results}, indent=2))

    if args.verbose:
        total = meta.get("total", len(results))
        print(
            f"Wrote {len(results)} records (reported total={total}) to {output_path}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
