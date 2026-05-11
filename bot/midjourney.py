"""Extract Midjourney-style flags from arbitrary text."""
import re

_PARAM_PATTERNS = {
    "sref": re.compile(r"(?:^|\s)--sref\s+(\d+)"),
    "ar": re.compile(r"(?:^|\s)--ar\s+(\d+:\d+)"),
    "style": re.compile(r"(?:^|\s)--style\s+(\S+)"),
    "v": re.compile(r"(?:^|\s)--v\s+(\d+(?:\.\d+)?)"),
    "niji": re.compile(r"(?:^|\s)--niji\s+(\d+)"),
    "chaos": re.compile(r"(?:^|\s)--chaos\s+(\d+)"),
    "stylize": re.compile(r"(?:^|\s)--stylize\s+(\d+)"),
    "weird": re.compile(r"(?:^|\s)--weird\s+(\d+)"),
}


def extract_params(text: str | None) -> dict[str, str]:
    """Return dict of recognized Midjourney params present in `text`.

    Only matches `--flag value` patterns preceded by whitespace or string start,
    so flags embedded in URLs (e.g. "--ar" inside a path segment) don't trigger.
    """
    if not text:
        return {}
    out: dict[str, str] = {}
    for name, pattern in _PARAM_PATTERNS.items():
        m = pattern.search(text)
        if m:
            out[name] = m.group(1)
    return out
