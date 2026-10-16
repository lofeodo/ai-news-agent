# agents/render/text.py
#
# Escaping for everything that goes into the email.

import html as _html
import re


def esc(text) -> str:
    """HTML-escape text for the newsletter, and break up "{{" / "}}" so article text can never form the
    {{UNSUBSCRIBE_URL}} / {{PREFERENCES_URL}} placeholders that agent4 substitutes per subscriber."""
    return _html.escape(str(text)).replace("{{", "&#123;&#123;").replace("}}", "&#125;&#125;")


def safe_url(url: str | None) -> str:
    """Return url, HTML-escaped for use inside href="...", only if it uses http/https; else '#'.

    The scheme check stops javascript: URLs; the escaping stops a quote in the URL from ending the attribute.
    """
    if url and isinstance(url, str) and url.startswith(("https://", "http://")):
        return esc(url)
    return "#"


def strip_markdown_headers(text: str) -> str:
    """Remove markdown header lines that Claude may generate despite prompt instructions."""
    return re.sub(r"^#{1,6}\s+[^\n]*\n?", "", text, flags=re.MULTILINE).strip()
