import re
from urllib.parse import urlparse

_STATUS_PATH_RE = re.compile(
    r"^/(?P<username>[\w]+)/status/(?P<status_id>\d+)",
    re.IGNORECASE,
)

def normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("http://"):
        url = url.replace("http://", "https://", 1)
    if not url.startswith("https://"):
        url = "https://" + url
    url = url.replace("twitter.com", "x.com")
    url = url.replace("mobile.x.com", "x.com")
    url = re.sub(r"https://x\.com/i/web/status/(\d+)", r"https://x.com/i/status/\1", url)
    return url

def is_valid_status_url(url: str) -> bool:
    url = normalize_url(url)
    parsed = urlparse(url)
    if parsed.hostname not in ("x.com", "www.x.com"):
        return False
    if parsed.path and _STATUS_PATH_RE.match(parsed.path):
        return True
    if parsed.path and re.match(r"^/i/status/\d+", parsed.path, re.IGNORECASE):
        return True
    return False

def extract_status_id(url: str) -> str | None:
    url = normalize_url(url)
    parsed = urlparse(url)
    m = _STATUS_PATH_RE.match(parsed.path)
    if m:
        return m.group("status_id")
    m2 = re.match(r"^/i/status/(\d+)", parsed.path, re.IGNORECASE)
    if m2:
        return m2.group(1)
    return None
