"""Google Analytics / Search Console head injection.

Streamlit does not expose a supported way to add tags to <head>; the
accepted workaround is to patch the installed streamlit package's own
static/index.html at startup. This is safe on Streamlit Cloud because the
package is reinstalled fresh on every deploy, before the app (and this
patch) runs.
"""

from pathlib import Path

import streamlit as st

from app.config import config_value

_MARKER = "<!-- ai-riviera-analytics -->"


def inject_google_analytics() -> None:
    measurement_id = config_value("GA_MEASUREMENT_ID")
    site_verification = config_value("GOOGLE_SITE_VERIFICATION")
    if not measurement_id and not site_verification:
        return

    try:
        index_path = Path(st.__file__).resolve().parent / "static" / "index.html"
        markup = index_path.read_text(encoding="utf-8")
    except Exception:
        return

    if _MARKER in markup or "<head>" not in markup:
        return

    tags = [_MARKER]
    if measurement_id:
        tags.append(
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={measurement_id}"></script>'
            "<script>"
            "window.dataLayer = window.dataLayer || [];"
            "function gtag(){dataLayer.push(arguments);}"
            "gtag('js', new Date());"
            f"gtag('config', '{measurement_id}');"
            "</script>"
        )
    if site_verification:
        tags.append(f'<meta name="google-site-verification" content="{site_verification}" />')

    patched = markup.replace("<head>", "<head>\n    " + "\n    ".join(tags), 1)
    try:
        index_path.write_text(patched, encoding="utf-8")
    except Exception:
        pass
