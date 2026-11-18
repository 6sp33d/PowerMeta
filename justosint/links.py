"""Reading links out of pages, unwrapping the redirect links search engines use, and
deciding which links are the files that were asked for.

Everything here is plain text handling: nothing in this module touches the network.
"""

from __future__ import annotations

import base64
import binascii
import html
import os
import re
from collections import namedtuple
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urlsplit

DEFAULT_FILE_TYPES = ["pdf", "docx", "xlsx", "doc", "xls", "pptx", "ppt"]

# One file the Internet Archive knows about: its address on the site, and where the Archive keeps a copy.
ArchiveEntry = namedtuple("ArchiveEntry", ["link", "archive_copy"])

# A copy is fetched as <prefix><timestamp>id_/<address as captured>; "id_" asks for the file as it was saved.
ARCHIVE_COPY_PREFIX = "https://web.archive.org/web/"

_HREF = re.compile(r"""<a\s[^>]*?href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_FILE_TYPE = re.compile(r"[a-z0-9]{1,12}")
_HOST = re.compile(r"[^\W_]([^\W_]|[.\-])*[^\W_]|[^\W_]")
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._%+=@,~() \-]")
# Windows device names, also when spaces come before the dot ("nul .pdf")
_NAME_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9]) *(\.|$)", re.I)
_NAME_DEFUSE = re.compile(r"([.\-]|exiftool)", re.I)
_FORM = re.compile(r"<form\b[^>]*>(.*?)</form>", re.I | re.S)
_INPUT = re.compile(r"<input\b[^>]*>", re.I)
_FIELD_NAME = re.compile(r"""\bname\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_FIELD_VALUE = re.compile(r"""\bvalue\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)


def parse_file_types(text):
    """Turn "pdf, .DOCX,xlsx" (or a list) into clean lower-case extensions. Invalid entries are skipped."""
    entries = text if isinstance(text, (list, tuple)) else str(text or "").split(",")
    types = []
    for entry in entries:
        clean = str(entry).strip().lstrip(".").lower()
        if clean and _FILE_TYPE.fullmatch(clean) and clean not in types:
            types.append(clean)
    return types


def host_from_target(target):
    """Reduce "https://www.TargetDomain.com/path" or "*.targetdomain.com" to a bare host name.

    A name with letters outside ASCII (bücher.de) is given in the form the search engines and the
    Internet Archive use for it (xn--bcher-kva.de).
    """
    name = str(target or "").strip().lower()
    name = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", name)
    name = re.sub(r"[/?#].*$", "", name)
    name = re.sub(r"^.*@", "", name)
    name = re.sub(r":\d+$", "", name)
    name = re.sub(r"^\*\.", "", name)
    name = name.strip(".")
    if not name.isascii():
        try:
            name = name.encode("idna").decode("ascii")
        except UnicodeError:
            pass
    return name


def is_valid_host(host):
    return bool(host) and _HOST.fullmatch(host) is not None


class _PageReader(HTMLParser):
    """Collects the address of every link in a page, and the fields of every form."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.forms = []
        self.bing_results = []  # the first link of each element with class "b_algo"
        self._form = None
        self._in_result = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href":
                    if value:
                        self.links.append(value)
                        if self._in_result:
                            self.bing_results.append(value)
                            self._in_result = False
                    break
        elif tag == "li":
            self._in_result = "b_algo" in (dict(attrs).get("class") or "").split()
        elif tag == "form":
            self._form = {}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None:
            fields = dict(reversed(attrs))  # the first of two attributes with one name counts
            if fields.get("name"):
                self._form[fields["name"]] = fields.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "li":
            self._in_result = False


def _read_page(page):
    """The links and forms of a page, or None if the page cannot be read as HTML."""
    reader = _PageReader()
    try:
        # The parser is not told that the page has ended. Then it leaves a tag that is never finished
        # alone, as a browser does, instead of looking for its end again and again (which took older
        # Python versions a very long time on a page made of such tags).
        reader.feed(page or "")
    except Exception:  # before Python 3.10 the parser gives up on some malformed markup
        return None
    return reader


def links_from_html(page):
    """The href of every link in a page, with HTML entities such as &amp; decoded."""
    reader = _read_page(page)
    if reader is not None:
        return reader.links
    links = []
    for double, single, bare in _HREF.findall(page or ""):
        value = double or single or bare
        if value:
            links.append(html.unescape(value))
    return links


def _forms_from_html(page):
    """The fields of every form in a page: a list of {name: value}."""
    reader = _read_page(page)
    if reader is not None:
        return reader.forms
    forms = []
    for body in _FORM.findall(page or ""):
        fields = {}
        for tag in _INPUT.findall(body):
            name = _FIELD_NAME.search(tag)
            field = (name.group(1) or name.group(2) or "") if name else ""
            if field:
                value = _FIELD_VALUE.search(tag)
                fields[field] = html.unescape((value.group(1) or value.group(2) or "") if value else "")
        forms.append(fields)
    return forms


def _split(link):
    try:
        parts = urlsplit(link)
        parts.port  # raises ValueError for a malformed port
        return parts
    except ValueError:
        return None


def _is_http(link):
    parts = _split(link)
    return parts is not None and parts.scheme in ("http", "https") and bool(parts.hostname)


def unwrap_bing(link):
    """Bing wraps each result as https://www.bing.com/ck/a?...&u=a1<base64url of the real address>.

    Returns the real address, the link unchanged if it is not wrapped, or None if it cannot be decoded
    into an http(s) address.
    """
    if not re.match(r"(?:https?:)?//(?:www\.)?bing\.com/ck/a\?", link, re.I):
        return link
    match = re.search(r"[?&]u=[A-Za-z0-9]{2}([A-Za-z0-9_\-]+)", link)
    if not match:
        return None
    encoded = match.group(1).replace("-", "+").replace("_", "/")
    encoded += "=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    return decoded if re.match(r"https?://", decoded, re.I) else None


def unwrap_duckduckgo(link):
    """DuckDuckGo gives results as plain addresses or wrapped as //duckduckgo.com/l/?uddg=<url-encoded address>."""
    if not re.match(r"(?:https?:)?//(?:www\.)?duckduckgo\.com/l/\?", link, re.I):
        return link
    match = re.search(r"[?&]uddg=([^&#]+)", link)
    if not match:
        return None
    decoded = unquote(match.group(1))
    return decoded if re.match(r"https?://", decoded, re.I) else None


def classify_google_link(link, own_hosts=()):
    """Sort a link from a Google results page into one of three kinds.

    ("address", url)   a result address that can be used as it is (also unwrapped from /url?q=...)
    ("redirect", link) a google.com/goto?url=... link: the address is hidden and has to be resolved
    ("other", None)    one of Google's own links, or something that is not an http(s) address

    own_hosts names further hosts ("host" or "host:port") to treat as Google's own.
    """
    parts = _split(link)
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        return ("other", None)
    host = parts.hostname.lower()
    is_google = re.fullmatch(r"(?:[a-z0-9\-]+\.)*google\.[a-z.]{2,6}", host) is not None
    if not is_google and parts.netloc.lower() not in own_hosts and host not in own_hosts:
        return ("address", link)
    if parts.path == "/goto":
        return ("redirect", link)
    if parts.path == "/url":
        query = parse_qs(parts.query)
        for name in ("q", "url"):
            for value in query.get(name, []):
                if _is_http(value):
                    return ("address", value)
    return ("other", None)


def is_on_domain(link, domain):
    """True if the link is an http(s) address on the domain or one of its sub-domains."""
    parts = _split(link)
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname.lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def _path_and_query(parts):
    return (parts.path or "/") + ("?" + parts.query if parts.query else "")


def _names_type(text, file_type):
    """True if the text holds ".pdf" (or another type) that is not followed by a letter or digit, so that
    a search for doc does not pick up docx."""
    return re.search(r"\." + re.escape(file_type) + r"(?![A-Za-z0-9])", text, re.I) is not None


def is_wanted_file(link, domain, file_type):
    """True if the link is on the domain and names a file of the wanted type, in its path or its query
    (https://example.com/get.php?file=report.pdf)."""
    if not is_on_domain(link, domain):
        return False
    return _names_type(_path_and_query(urlsplit(link)), file_type)


def wanted_type(link, domain, file_types):
    """The first of the wanted file types that the link names, or None."""
    for file_type in file_types:
        if is_wanted_file(link, domain, file_type):
            return file_type
    return None


def plain_ascii(link):
    """Percent-encode anything outside printable ASCII, the way search engines normally give addresses."""
    if re.search(r"[^\x21-\x7E]", link):
        return quote(link, safe="%/:?#[]@!$&'()*+,;=~-._")
    return link


def link_key(link):
    """The part of a link that identifies the file: host (and an unusual port), path and query.

    "http://" or "https://" is left out, so the two addresses of one file compare as equal.
    """
    parts = _split(link)
    if parts is None or not parts.scheme or not parts.hostname:
        return link
    key = parts.hostname.lower()
    default_port = {"http": 80, "https": 443}.get(parts.scheme)
    if parts.port is not None and parts.port != default_port:
        key += ":" + str(parts.port)
    return key + (parts.path or "/") + ("?" + parts.query if parts.query else "")


def normalise(link):
    """Lower-case the host and drop a default port (http://Example.com:80/a.pdf -> http://example.com/a.pdf)."""
    parts = _split(link)
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        return link
    return parts.scheme + "://" + link_key(link)


def safe_file_name(link, file_types=DEFAULT_FILE_TYPES):
    """Turn the last part of a link into a file name that is safe on Windows and for command lines.

    A name with an ending always ends in one of the wanted file types. The site must not be able to choose
    another one: a download saved as argparse.py or setup.exe could be run by mistake, or be picked up by
    a program that is started in the folder. So get.php?file=report.pdf is saved as get.php.pdf, and a
    link that names no wanted type gets ".download" added.
    """
    parts = _split(link)
    path = parts.path if parts is not None else ""
    name = path.rsplit("/", 1)[-1]

    # Keep letters, digits and a few harmless symbols; everything else becomes "_"
    name = _NAME_UNSAFE.sub("_", name).strip().rstrip(". ")

    ending = name.rsplit(".", 1)[1].lower() if "." in name else ""
    if ending not in file_types:
        named = None
        if parts is not None:
            named = next((t for t in file_types if _names_type(_path_and_query(parts), t)), None)
        if named:
            name = (name or "download") + "." + named
        elif "." in name:
            name += ".download"

    if len(name) > 150:
        extension = os.path.splitext(name)[1]
        if len(extension) > 20:
            extension = ""
        name = name[: 150 - len(extension)] + extension

    if not name or re.fullmatch(r"[. ]+", name):
        name = "download"
    if _NAME_RESERVED.match(name):
        name = "_" + name
    # A download must never be taken for ExifTool itself, for its configuration file (.ExifTool_config),
    # or for a command-line option (a name that starts with "-")
    if _NAME_DEFUSE.match(name):
        name = "_" + name
    return name


def file_type_of(link, file_types=DEFAULT_FILE_TYPES):
    """The wanted file type a link counts as: the ending its file is saved with (see safe_file_name).
    None if that is not one of the wanted types, which only a link from a list of links can be."""
    name = safe_file_name(link, file_types)
    ending = name.rsplit(".", 1)[1].lower() if "." in name else ""
    return ending if ending in file_types else None


def count_bing_results(page):
    """Each organic Bing result sits in an element with class "b_algo"."""
    return len(re.findall(r'class="b_algo[ "]', page or ""))


def bing_results(page):
    """The address of each organic Bing result on a page (unwrapped; None for one that cannot be read)."""
    reader = _read_page(page)
    if reader is not None:
        hrefs = reader.bing_results
    else:
        hrefs = [html.unescape(href) for href in re.findall(r'class="b_algo[ "][^>]*>.*?<a\s[^>]*?href="([^"]*)"', page or "", re.S)]
    return [unwrap_bing(href) for href in hrefs]


def bing_keeps_to_site(page, domain):
    """Whether a page of Bing results is an answer to a "site:" search for the domain.

    Returns (number of results, how many of them are on the domain, verdict). An answer that keeps to the
    site has results of that site only; Bing answers some searches with other sites instead. One stray
    result does not decide either way: the verdict is True when most results are on the domain.
    """
    results = bing_results(page)
    on_domain = sum(1 for address in results if address and is_on_domain(address, domain))
    return len(results), on_domain, bool(results) and on_domain * 2 > len(results)


def count_duckduckgo_results(page):
    """Each DuckDuckGo result title is a link with class "result__a"."""
    return len(re.findall(r'class="result__a"', page or ""))


def is_duckduckgo_bot_check(page):
    """The page DuckDuckGo shows instead of results ("Unfortunately, bots use DuckDuckGo too")."""
    return count_duckduckgo_results(page) == 0 and re.search(r"anomaly-modal|challenge-form", page or "") is not None


def duckduckgo_next_form(page, after=0):
    """The fields of the "Next" form on a DuckDuckGo results page, or None on the last page.

    The form carries the query (q), the offset of the next page (s) and the tokens DuckDuckGo wants back.
    The "Next" form is the one whose offset lies just beyond the page that was read ("Previous" points
    backwards).
    """
    best, best_offset = None, None
    for fields in _forms_from_html(page):
        if "q" not in fields or not re.fullmatch(r"[0-9]{1,9}", fields.get("s", "")):
            continue
        offset = int(fields["s"])
        # The nearest offset beyond this page: no other form on the page can take the place of "Next"
        if offset > after and (best is None or offset < best_offset):
            best, best_offset = fields, offset
    return best


def archive_filter(file_types):
    """The filter for the Internet Archive's index, which applies it as a Java regular expression to the
    whole address. It skips the host name (a host such as www.doc.gov must not count as a file type) and
    then looks for ".pdf" (or another wanted type) that is not followed by a letter or digit.
    """
    types = "|".join(re.escape(file_type) for file_type in file_types)
    return "original:(?i)[a-z]+://[^/?#]+[/?].*\\.(" + types + ")([^a-z0-9].*)?"


def parse_archive_index(text, domain, file_types):
    """Read the answer of the Internet Archive's index: one "address timestamp" pair per line.

    Returns an ArchiveEntry for each wanted file on the domain.
    """
    entries, seen = [], set()
    for line in (text or "").split("\n"):
        parts = line.split()
        if len(parts) != 2:
            continue
        original, timestamp = parts
        if not re.fullmatch(r"\d{14}", timestamp):
            continue
        # An address in the index can hold anything, also characters that do not belong in an address
        original = plain_ascii(original)
        # Captured addresses often carry the default port (http://example.com:80/a.pdf)
        link = normalise(original)
        if wanted_type(link, domain, file_types) is None or link in seen:
            continue
        seen.add(link)
        entries.append(ArchiveEntry(link, ARCHIVE_COPY_PREFIX + timestamp + "id_/" + original))
    return entries
