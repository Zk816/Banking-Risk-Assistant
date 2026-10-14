import argparse
import sys

import uvicorn

from fin_assistant.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the API.")
    parser.add_argument("--host", default=settings.api_host)
    parser.add_argument("--port", type=int, default=settings.api_port)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload on code changes. Development only — it reloads the models too, six seconds each time.",
    )
    args = parser.parse_args()

    print(f"\n  http://{args.host}:{args.port}/docs\n")
    uvicorn.run(
        "fin_assistant.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
