"""Start the API: `python -m app [--host 0.0.0.0] [--port 8000] [--workers 4] [--reload]`.

Same as `uvicorn app.main:app`, but with uvicorn's X-Forwarded-For handling off (--no-proxy-headers),
so only TRUSTED_PROXIES decides whose X-Forwarded-For is believed (app/ratelimit.py, client_ip).
uvicorn's default trusts 127.0.0.1, so anything connecting from the same machine could set its own IP.
"""
import argparse

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="Start the Scheme API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    args = parser.parse_args(argv)
    uvicorn.run("app.main:app", host=args.host, port=args.port, workers=args.workers, reload=args.reload,
                proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
