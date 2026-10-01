#!/usr/bin/env python
"""Run the Vitae dashboard: python run.py"""
import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
        # Windows: uvicorn would otherwise use ProactorEventLoop, which psycopg's
        # async driver rejects. See app.main.selector_loop_factory.
        loop="app.main:selector_loop_factory",
    )


if __name__ == "__main__":
    main()
