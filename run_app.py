"""Prepare crawler-facing assets, then start AI Riviera's Streamlit server."""

import sys

from app.analytics import prepare_static_assets


def main() -> None:
    prepare_static_assets()

    # Streamlit's CLI reads sys.argv directly. Keeping all trailing arguments
    # lets Render inject --server.port or other deployment settings.
    sys.argv = ["streamlit", "run", "app/ui.py", *sys.argv[1:]]
    from streamlit.web import cli as streamlit_cli

    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    main()
