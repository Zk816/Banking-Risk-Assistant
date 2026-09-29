"""Run the HTTP service.

Run: uv run python scripts/serve.py
     uv run python scripts/serve.py --reload   # for development
"""

import argparse
import sys

import uvicorn

from kzbank.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the API.")
    parser.add_argument("--host", default=settings.api_host)
    parser.add_argument("--port", type=int, default=settings.api_port)
    parser.add_argument("--reload", action="store_true",
                        help="Reload on code changes. Development only — it reloads "
                             "the models too, six seconds each time.")
    args = parser.parse_args()

    print(f"\n  http://{args.host}:{args.port}/docs\n")
    uvicorn.run(
        "kzbank.api.main:app",
        host=args.host, port=args.port, reload=args.reload,
        log_config=None,  # our own logging_setup owns the handlers
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
