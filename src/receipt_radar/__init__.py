"""receipt-radar: pull your own grocery digital receipts into local, structured data."""

from .cli import app


def main() -> None:
    app()
