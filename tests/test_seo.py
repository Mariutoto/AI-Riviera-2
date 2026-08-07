import json
import xml.etree.ElementTree as ET

from app.analytics import SEO_DESCRIPTION, SEO_TITLE, robots_txt, seo_head_markup, sitemap_xml


def test_head_markup_contains_core_seo_signals():
    markup = seo_head_markup()

    assert SEO_TITLE in markup
    assert SEO_DESCRIPTION in markup
    assert '<link rel="canonical" href="https://airiviera.org/" />' in markup
    assert '<meta property="og:locale" content="fr_CH" />' in markup

    payload = markup.split('<script type="application/ld+json">', 1)[1].split(
        "</script>", 1
    )[0]
    structured_data = json.loads(payload)
    assert structured_data["@context"] == "https://schema.org"
    assert structured_data["@graph"][1]["inLanguage"] == "fr-CH"


def test_optional_analytics_and_verification_are_included():
    markup = seo_head_markup("G-TEST123", "verification-token")

    assert "googletagmanager.com/gtag/js?id=G-TEST123" in markup
    assert 'google-site-verification" content="verification-token"' in markup


def test_crawler_files_are_valid_and_reference_the_canonical_site():
    robots = robots_txt()
    assert "User-agent: *" in robots
    assert "Sitemap: https://airiviera.org/sitemap.xml" in robots

    root = ET.fromstring(sitemap_xml())
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    assert root.findtext("sm:url/sm:loc", namespaces=namespace) == "https://airiviera.org/"
