"""Static SEO, Google Analytics/Search Console injection, and favicon fix.

Streamlit does not expose a supported way to add tags to <head>, or to
replace its default favicon.png; the accepted workaround is to patch the
installed streamlit package's own static files at startup. This is safe on
Streamlit Cloud/Render because the package is reinstalled fresh on every
deploy, before the app (and this patch) runs.
"""

import json
from pathlib import Path

import streamlit as st

from app.config import config_value

SITE_URL = "https://airiviera.org/"
SEO_TITLE = "AI Riviera – Assistant de recherche dans les documents publics communaux"
SEO_DESCRIPTION = (
    "AI Riviera est un assistant de recherche à but non lucratif pour explorer "
    "les documents publics des communes de la Riviera vaudoise et consulter "
    "directement les sources officielles."
)

_HEAD_START = "<!-- ai-riviera-head-start -->"
_HEAD_END = "<!-- ai-riviera-head-end -->"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def seo_head_markup(
    measurement_id: str | None = None,
    site_verification: str | None = None,
) -> str:
    """Return the idempotent, server-rendered head block used by crawlers."""
    structured_data = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": f"{SITE_URL}#organization",
                "name": "AI Riviera",
                "url": SITE_URL,
                "description": SEO_DESCRIPTION,
                "logo": f"{SITE_URL}favicon.png",
            },
            {
                "@type": "WebSite",
                "@id": f"{SITE_URL}#website",
                "url": SITE_URL,
                "name": "AI Riviera",
                "description": SEO_DESCRIPTION,
                "inLanguage": "fr-CH",
                "publisher": {"@id": f"{SITE_URL}#organization"},
            },
        ],
    }
    tags = [
        _HEAD_START,
        f'<meta name="description" content="{SEO_DESCRIPTION}" />',
        '<meta name="robots" content="index, follow, max-image-preview:large" />',
        f'<link rel="canonical" href="{SITE_URL}" />',
        '<meta property="og:type" content="website" />',
        '<meta property="og:locale" content="fr_CH" />',
        '<meta property="og:site_name" content="AI Riviera" />',
        f'<meta property="og:title" content="{SEO_TITLE}" />',
        f'<meta property="og:description" content="{SEO_DESCRIPTION}" />',
        f'<meta property="og:url" content="{SITE_URL}" />',
        '<meta name="twitter:card" content="summary" />',
        f'<meta name="twitter:title" content="{SEO_TITLE}" />',
        f'<meta name="twitter:description" content="{SEO_DESCRIPTION}" />',
        '<script type="application/ld+json">'
        + json.dumps(structured_data, ensure_ascii=False, separators=(",", ":"))
        + "</script>",
    ]
    if measurement_id:
        tags.extend(
            [
                f'<script async src="https://www.googletagmanager.com/gtag/js?id={measurement_id}"></script>',
                "<script>"
                "window.dataLayer = window.dataLayer || [];"
                "function gtag(){dataLayer.push(arguments);}"
                "gtag('js', new Date());"
                f"gtag('config', '{measurement_id}');"
                "</script>",
            ]
        )
    if site_verification:
        tags.append(
            f'<meta name="google-site-verification" content="{site_verification}" />'
        )
    tags.append(_HEAD_END)
    return "\n    ".join(tags)


def robots_txt() -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n"


def sitemap_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{SITE_URL}</loc>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )


def inject_google_analytics() -> None:
    """Patch Streamlit's initial HTML with stable SEO and optional analytics."""
    measurement_id = config_value("GA_MEASUREMENT_ID")
    site_verification = config_value("GOOGLE_SITE_VERIFICATION")

    try:
        index_path = Path(st.__file__).resolve().parent / "static" / "index.html"
        markup = index_path.read_text(encoding="utf-8")
    except Exception:
        return

    if "<head>" not in markup:
        return

    if _HEAD_START in markup and _HEAD_END in markup:
        before, remainder = markup.split(_HEAD_START, 1)
        _, after = remainder.split(_HEAD_END, 1)
        markup = before + after.lstrip("\n")

    patched = markup.replace('<html lang="en">', '<html lang="fr">', 1)
    patched = patched.replace("<title>Streamlit</title>", f"<title>{SEO_TITLE}</title>", 1)
    patched = patched.replace(
        "<head>",
        "<head>\n    " + seo_head_markup(measurement_id, site_verification),
        1,
    )
    try:
        index_path.write_text(patched, encoding="utf-8")
    except Exception:
        pass


def write_crawler_files() -> None:
    """Expose real crawler files instead of Streamlit's HTML fallback."""
    try:
        static_dir = Path(st.__file__).resolve().parent / "static"
        (static_dir / "robots.txt").write_text(robots_txt(), encoding="utf-8")
        (static_dir / "sitemap.xml").write_text(sitemap_xml(), encoding="utf-8")
    except Exception:
        pass


def prepare_static_assets() -> None:
    """Prepare every crawler-facing asset before or during app startup."""
    inject_google_analytics()
    use_custom_favicon()
    write_crawler_files()


def use_custom_favicon() -> None:
    """Serve assets/favicon.png at Streamlit's fixed /favicon.png route.

    Crawlers (Google Search) read this file straight off disk and never run
    the app's JS, so st.set_page_config(page_icon=...) alone never reaches
    them — it only updates the browser tab icon after the app has loaded.
    """
    try:
        source = _PROJECT_ROOT / "assets" / "favicon.png"
        target = Path(st.__file__).resolve().parent / "static" / "favicon.png"
        source_bytes = source.read_bytes()
        if target.exists() and target.read_bytes() == source_bytes:
            return
        target.write_bytes(source_bytes)
    except Exception:
        pass
