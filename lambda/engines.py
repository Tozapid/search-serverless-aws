"""Search engines that answer without an API key.

None of the big engines has a free search API any more, so each one is read
the way a browser without JavaScript reads it:

- Google News and Bing News give RSS, the steadiest of all;
- Yahoo, DuckDuckGo and Brave Search give plain HTML pages, parsed here.

Some are missing on purpose. google.com serves no web results without
JavaScript and turns away requests from cloud addresses, and its Programmable
Search API takes no new customers: Google News is Google's part. Bing's web
results, as RSS or HTML, are unrelated to the query when a program asks;
Yahoo shows Bing's index, so Yahoo is Bing's part.

DuckDuckGo answers a program with a captcha and Brave with HTTP 429, from
AWS and often from home too. An administrator can give such engines a list of
proxies (SOCKS4, SOCKS5 or HTTP): search() takes `channels`, the ways to
reach the engine in order, each None (direct) or a dict of scheme, host,
port, username and password, and fetch() opens its connections through the
one in use. socks5h resolves names on the proxy, socks5 here. When a channel
fails, whatever the error, the next one is tried. A warm function remembers
the channel that worked and starts with it, and skips for FAILED_SECONDS the
ones that failed, so a digest of many searches does not knock on a dead proxy
again and again; at most `tries` channels are asked for one search.

An engine may change its page or refuse an address of AWS at any time; it
raises EngineError then, and the digest names the engine that failed while
the others still count.

Engines do not like several requests at once from one address: a combined
digest reads many searches together, and an engine then resets the
connection. So the requests to one engine wait in a queue (a lock per engine)
and keep a pause after each other (GAP), while different engines are asked at
the same time. A request that fails on the way (a reset or lost connection, a
timeout, HTTP 429 or 5xx) is tried again after a longer pause, up to
`retries` times, on the last channel only: with more channels left the next
one is asked at once. A captcha or a page that cannot be read is not tried
again on the same channel.

search(engine, query, limit, market, period) returns a list of results, each
{"title", "url", "snippet", "published", "source"}; market is a locale such
as "ru-RU", period "any", "day", "week" or "month".
"""

import functools
import gzip
import hashlib
import http.client
import http.cookiejar
import html
import html.parser
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

TIMEOUT = 10
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)
SNIPPET_LIMIT = 400
# Seconds between two requests to the same engine, and the pauses before the
# tries after a failure.
GAP = {"duckduckgo": 2.0, "brave": 2.0, "yahoo": 1.0}
DEFAULT_GAP = 0.5
BACKOFF = (3.0, 8.0)
RETRIES = 2
# Channels asked for one search at most, and how long a failed one rests.
TRIES = 3
FAILED_SECONDS = 600

# In the order of the page.
ENGINES = {
    "google_news": "Google News",
    "bing_news": "Bing News",
    "yahoo": "Yahoo",
    "duckduckgo": "DuckDuckGo",
    "brave": "Brave Search",
}

PERIODS = ("any", "day", "week", "month")

# Locales of the results: language and country.
MARKETS = (
    "ru-RU", "en-US", "en-GB", "de-DE", "fr-FR", "it-IT", "es-ES",
    "uk-UA", "pl-PL", "nl-NL", "pt-BR", "tr-TR", "ja-JP",
)


class EngineError(Exception):
    def __init__(self, message, transient=False):
        super().__init__(message)
        # Worth another try: the connection failed, not the engine's answer.
        self.transient = transient


class Gate:
    """One request at a time to an engine, GAP seconds apart."""

    def __init__(self, gap):
        self.gap = gap
        self.lock = threading.Lock()
        self.last = 0.0

    def __enter__(self):
        self.lock.acquire()
        wait = self.last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        return self

    def __exit__(self, *args):
        self.last = time.monotonic()
        self.lock.release()
        return False


_gates = {}
_gates_lock = threading.Lock()


def gate(engine):
    with _gates_lock:
        if engine not in _gates:
            _gates[engine] = Gate(GAP.get(engine, DEFAULT_GAP))
        return _gates[engine]


def search(engine, query, limit, market="en-US", period="any", retries=RETRIES, channels=(None,), tries=TRIES):
    reader = READERS.get(engine)
    if not reader:
        raise EngineError(f"unknown engine {engine}")
    language, country = market.split("-")
    channels = list(channels) or [None]
    order = arrange(engine, channels)[:tries]
    if not order:
        raise EngineError(f"all {len(channels)} channels failed within the last {FAILED_SECONDS // 60} minutes")
    failures = []
    for number, proxy in enumerate(order):
        last = number == len(order) - 1
        try:
            results = read(engine, reader, (query, limit, language, country, period), proxy, retries if last else 0)
        except EngineError as exc:
            if len(channels) > 1:
                note(engine, proxy, False)
            failures.append(str(exc) if len(channels) == 1 else f"{channel_name(proxy)}: {exc}")
            continue
        if len(channels) > 1:
            note(engine, proxy, True)
        break
    else:
        raise EngineError("; ".join(failures))
    clean = []
    for item in results:
        url = (item.get("url") or "").strip()
        title = squash(item.get("title"))
        if not url.startswith(("http://", "https://")) or not title:
            continue
        clean.append({
            "title": title[:300],
            "url": url,
            "snippet": squash(item.get("snippet"))[:SNIPPET_LIMIT],
            "published": item.get("published") or "",
            "source": squash(item.get("source"))[:100],
        })
        if len(clean) >= limit:
            break
    return clean


def read(engine, reader, args, proxy, retries):
    attempt = 0
    while True:
        try:
            with gate(engine):
                _context.proxy = proxy
                try:
                    return reader(*args)
                finally:
                    _context.proxy = None
        except EngineError as exc:
            if not exc.transient or attempt >= retries:
                raise
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            attempt += 1


# Per engine: the channel that worked last, and when each one failed.
_working = {}
_failed = {}
_channels_lock = threading.Lock()


def channel_name(proxy):
    return f"{proxy['host']}:{proxy['port']}" if proxy else "direct"


def channel_key(proxy):
    return proxy_url(proxy, secret=False) if proxy else "direct"


def arrange(engine, channels):
    """The channels to try: the one that worked last first, then the rest in
    their order, without those that failed within FAILED_SECONDS. A single
    channel is always tried."""
    if len(channels) == 1:
        return channels
    now = time.monotonic()
    with _channels_lock:
        working = _working.get(engine)
        failed = {key: at for (name, key), at in _failed.items() if name == engine}
    fresh = [proxy for proxy in channels if channel_key(proxy) not in failed or now - failed[channel_key(proxy)] >= FAILED_SECONDS]
    first = [proxy for proxy in fresh if channel_key(proxy) == working]
    return first + [proxy for proxy in fresh if channel_key(proxy) != working]


def note(engine, proxy, worked):
    key = channel_key(proxy)
    with _channels_lock:
        if worked:
            _working[engine] = key
            _failed.pop((engine, key), None)
        else:
            _failed[(engine, key)] = time.monotonic()
            if _working.get(engine) == key:
                del _working[engine]


def forget_channels():
    """After the list changes: every channel gets a new chance."""
    with _channels_lock:
        _working.clear()
        _failed.clear()


# HTTP ----------------------------------------------------------------------

# The proxy of the engine being read in this thread, set by search().
_context = threading.local()

PROXY_SCHEMES = ("socks5h", "socks5", "socks4", "http", "https")


class SocksConnection(http.client.HTTPConnection):
    def __init__(self, *args, proxy=None, **kwargs):
        self.proxy = proxy
        super().__init__(*args, **kwargs)

    def connect(self):
        import socks

        proxy = self.proxy
        kind = socks.SOCKS4 if proxy["scheme"] == "socks4" else socks.SOCKS5
        self.sock = socks.create_connection(
            (self.host, self.port),
            timeout=self.timeout,
            proxy_type=kind,
            proxy_addr=proxy["host"],
            proxy_port=int(proxy["port"]),
            proxy_rdns=proxy["scheme"] == "socks5h",
            proxy_username=proxy.get("username") or None,
            proxy_password=proxy.get("password") or None,
        )


class SocksTlsConnection(http.client.HTTPSConnection):
    def __init__(self, *args, proxy=None, **kwargs):
        self.proxy = proxy
        super().__init__(*args, **kwargs)

    def connect(self):
        SocksConnection.connect(self)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class SocksHandler(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    def __init__(self, proxy):
        urllib.request.HTTPHandler.__init__(self)
        urllib.request.HTTPSHandler.__init__(self)
        self.proxy = proxy

    def http_open(self, req):
        return self.do_open(functools.partial(SocksConnection, proxy=self.proxy), req)

    def https_open(self, req):
        return self.do_open(functools.partial(SocksTlsConnection, proxy=self.proxy), req, context=self._context)


def proxy_url(proxy, secret=True):
    auth = ""
    if proxy.get("username"):
        password = proxy.get("password") or ""
        auth = urllib.parse.quote(proxy["username"], safe="")
        if password:
            auth += ":" + (urllib.parse.quote(password, safe="") if secret else "****")
        auth += "@"
    return f"{proxy['scheme']}://{auth}{proxy['host']}:{proxy['port']}"


def parse_proxy(line, scheme="socks5h"):
    """A proxy from one line of a list: scheme://user:password@host:port,
    user:password@host:port, host:port:user:password or host:port; a line
    without a scheme gets `scheme`. None if the line is not one of these."""
    line = line.strip().rstrip("/")
    if "://" in line:
        scheme, _, line = line.partition("://")
        scheme = scheme.lower()
    username = password = ""
    if "@" in line:
        auth, _, line = line.rpartition("@")
        username, _, password = (urllib.parse.unquote(part) for part in auth.partition(":"))
        host, _, port = line.partition(":")
    else:
        host, _, rest = line.partition(":")
        port, _, auth = rest.partition(":")
        username, _, password = auth.partition(":")
    if scheme not in PROXY_SCHEMES or not port.isdigit():
        return None
    return {"scheme": scheme, "host": host, "port": int(port), "username": username, "password": password}


def opener_for(proxy):
    handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
    if proxy and proxy["scheme"].startswith("socks"):
        handlers.append(SocksHandler(proxy))
    elif proxy:
        url = proxy_url(proxy)
        handlers.append(urllib.request.ProxyHandler({"http": url, "https": url}))
    return urllib.request.build_opener(*handlers)


def check_ip(proxy):
    """The address the internet sees, through the proxy if one is given."""
    _context.proxy = proxy
    try:
        _, text = fetch("https://checkip.amazonaws.com/")
    finally:
        _context.proxy = None
    return text.strip()


def fetch(url, data=None, language="en"):
    request = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": f"{language},en;q=0.5",
        "Accept-Encoding": "gzip",
    })
    # Yahoo sends a new visitor through a redirect that sets a cookie first.
    proxy = getattr(_context, "proxy", None)
    opener = opener_for(proxy)
    via = " via the proxy" if proxy else ""
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            body = response.read(3_000_000)
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, body.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise EngineError(f"HTTP {exc.code}{via}", transient=exc.code == 429 or exc.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EngineError(f"no answer{via} ({getattr(exc, 'reason', exc)})", transient=True) from None


def squash(value):
    """Plain text on one line: tags and entities out, spaces folded."""
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def rfc822_date(value):
    try:
        return parsedate_to_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""
    except (TypeError, ValueError):
        return ""


# RSS -----------------------------------------------------------------------

def rss_items(text):
    try:
        root = ET.fromstring(text.encode())
    except ET.ParseError:
        root = None
    if root is None or root.find("channel") is None:
        raise EngineError("the answer is not RSS; the engine may have refused the request")
    for item in root.iter("item"):
        source = item.find("source")
        yield {
            "title": item.findtext("title"),
            "url": (item.findtext("link") or "").strip(),
            "snippet": item.findtext("description"),
            "published": rfc822_date(item.findtext("pubDate")),
            "source": source.text if source is not None else "",
        }


def google_news(query, limit, language, country, period):
    when = {"day": " when:1d", "week": " when:7d", "month": " when:30d"}.get(period, "")
    params = {"q": query + when, "hl": f"{language}-{country}", "gl": country, "ceid": f"{country}:{language}"}
    _, text = fetch("https://news.google.com/rss/search?" + urllib.parse.urlencode(params), language=language)
    items = []
    for item in rss_items(text):
        # The title ends with " - <source>", which is shown on its own.
        if item["source"] and item["title"] and item["title"].endswith(" - " + item["source"]):
            item["title"] = item["title"][: -len(item["source"]) - 3]
        # The description repeats the title and the source.
        item["snippet"] = ""
        items.append(item)
    return items[:limit]



def bing_news(query, limit, language, country, period):
    params = {"format": "rss", "q": query, "count": str(min(limit, 50)), "setlang": language, "cc": country,
              "mkt": f"{language}-{country}"}
    interval = {"day": 'interval="7"', "week": 'interval="8"', "month": 'interval="9"'}.get(period)
    if interval:
        params["qft"] = interval
    _, text = fetch("https://www.bing.com/news/search?" + urllib.parse.urlencode(params), language=language)
    items = list(rss_items(text))
    for item in items:
        # Links go through Bing's click counter; the article is its url.
        item["url"] = query_value(item["url"], "url") or item["url"]
    return items


# HTML ----------------------------------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.children = []

    def classes(self):
        return (self.attrs.get("class") or "").split()

    def has_class(self, name):
        return name in self.classes()

    def text(self):
        parts = []
        for child in self.children:
            parts.append(child.text() if isinstance(child, Node) else child)
        return " ".join(part for part in parts if part)

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(self, tag=None, cls=None):
        return [node for node in self.walk()
                if (tag is None or node.tag == tag) and (cls is None or node.has_class(cls))]

    def find(self, tag=None, cls=None):
        found = self.find_all(tag, cls)
        return found[0] if found else None


class TreeBuilder(html.parser.HTMLParser):
    """A forgiving tree of the page: a closing tag closes the nearest open
    element of its name, one without an open element is ignored."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        node = Node(tag, {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if tag not in ("script", "style"):
            self.stack[-1].children.append(Node(tag, {name: value or "" for name, value in attrs}))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.stack[-1].children.append(data)


def parse_html(text):
    builder = TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


def query_value(url, name):
    try:
        values = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get(name)
    except ValueError:
        return ""
    return values[0] if values else ""


def yahoo_target(href):
    """Yahoo's links go through r.search.yahoo.com/.../RU=<url>/RK=...."""
    match = re.search(r"/RU=([^/]+)/R[KS]=", href)
    return urllib.parse.unquote(match.group(1)) if match else href


def yahoo(query, limit, language, country, period):
    params = {"p": query, "n": str(min(limit, 30)), "vl": f"lang_{language}", "vc": country.lower()}
    age = {"day": "d", "week": "w", "month": "m"}.get(period)
    if age:
        params["btf"] = age
    _, text = fetch("https://search.yahoo.com/search?" + urllib.parse.urlencode(params), language=language)
    root = parse_html(text)
    items = []
    for block in root.find_all("div", "algo"):
        heading = block.find("h3")
        link = (heading.find("a") if heading else None) or block.find("a")
        if not link or not link.attrs.get("href"):
            continue
        title = link.attrs.get("aria-label") or (heading.text() if heading else link.text())
        # Without aria-label the heading starts with the address of the site.
        if not link.attrs.get("aria-label"):
            crumb = link.find("span")
            if crumb and title.startswith(crumb.text()):
                title = title[len(crumb.text()):]
        snippet = block.find("div", "compText")
        items.append({
            "title": title,
            "url": yahoo_target(link.attrs["href"]),
            "snippet": snippet.text() if snippet else "",
        })
    if not items and "algo" not in text and len(text) < 20_000:
        raise EngineError("no results page; the engine may have refused the request")
    return items


def duckduckgo(query, limit, language, country, period):
    data = {"q": query, "kl": f"{country.lower()}-{language}", "b": ""}
    age = {"day": "d", "week": "w", "month": "m"}.get(period)
    if age:
        data["df"] = age
    _, text = fetch("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode(data).encode(), language=language)
    if "anomaly-modal" in text or "challenge-form" in text:
        raise EngineError("DuckDuckGo asked for a captcha; it does not let this address search now")
    root = parse_html(text)
    items = []
    for block in root.find_all("div", "result"):
        if block.has_class("result--ad"):
            continue
        link = block.find("a", "result__a")
        if not link:
            continue
        href = link.attrs.get("href", "")
        if href.startswith("//"):
            href = "https:" + href
        target = query_value(href, "uddg") or href
        snippet = block.find(None, "result__snippet")
        items.append({"title": link.text(), "url": target, "snippet": snippet.text() if snippet else ""})
    return items


def brave(query, limit, language, country, period):
    """Brave Search: its own index, not Google's or Bing's. Each web result is
    a div.snippet with data-type="web": the link, the title, the site name and
    the description."""
    params = {"q": query, "source": "web"}
    age = {"day": "pd", "week": "pw", "month": "pm"}.get(period)
    if age:
        params["tf"] = age
    _, text = fetch("https://search.brave.com/search?" + urllib.parse.urlencode(params), language=language)
    root = parse_html(text)
    items = []
    for block in root.find_all("div", "snippet"):
        if block.attrs.get("data-type") != "web":
            continue
        link = next((node for node in block.find_all("a") if node.attrs.get("href", "").startswith("http")), None)
        if not link:
            continue
        title = block.find(None, "title")
        site = block.find("div", "desktop-small-semibold")
        snippet = block.find("div", "generic-snippet")
        items.append({
            "title": title.text() if title else link.text(),
            "url": link.attrs["href"],
            "snippet": snippet.text() if snippet else "",
            "source": site.text() if site else "",
        })
    if not items and "captcha" in text.lower():
        raise EngineError("Brave Search asked for a captcha; it does not let this address search now")
    return items


READERS = {
    "google_news": google_news,
    "bing_news": bing_news,
    "yahoo": yahoo,
    "duckduckgo": duckduckgo,
    "brave": brave,
}


# Results -------------------------------------------------------------------

TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|yclid$|mc_|ref$|ref_src$|ocid$)")


def normal_url(url):
    """The same page under small differences: scheme, www., fragment,
    tracking parameters, a closing slash."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = urllib.parse.urlencode(sorted(
        (name, value) for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not TRACKING.match(name.lower())
    ))
    path = parts.path.rstrip("/") or "/"
    return f"{host}{path}" + (f"?{query}" if query else "")


def url_key(url):
    return hashlib.sha256(normal_url(url).encode()).hexdigest()[:16]


def merge(by_engine):
    """One list from the lists of several engines: the first places of every
    engine first, a page found by several engines once, with all of them."""
    merged = {}
    order = []
    longest = max((len(items) for items in by_engine.values()), default=0)
    for rank in range(longest):
        for engine, items in by_engine.items():
            if rank >= len(items):
                continue
            item = items[rank]
            key = url_key(item["url"])
            if key in merged:
                found = merged[key]
                if engine not in found["engines"]:
                    found["engines"].append(engine)
                if not found["snippet"] and item["snippet"]:
                    found["snippet"] = item["snippet"]
                continue
            merged[key] = {**item, "key": key, "engines": [engine]}
            order.append(key)
    return [merged[key] for key in order]
