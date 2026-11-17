"""Plain web requests: fetching pages (with cookies kept for the session) and downloading files."""

from __future__ import annotations

import http.client
import http.cookiejar
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zlib

from . import __version__
from .links import plain_ascii

# What the search engines and the target site are sent, unless --user-agent says otherwise
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)
# What a service that is made to be used by programs (the Internet Archive) is sent
PROGRAM_USER_AGENT = "PowerMeta/" + __version__

# No answer is read beyond these sizes: a server must not be able to fill the memory or the disk
MAX_PAGE_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024

# Everything a broken or hostile server can make urllib, http.client and zlib raise
_REQUEST_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, EOFError, zlib.error)


class FetchError(Exception):
    """A request that did not produce a page. status is the HTTP status, or 0 if there was no answer."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class Page:
    def __init__(self, status, text, url):
        self.status = status
        self.text = text
        self.url = url


def _reason(error):
    """A short, one-line description of what went wrong. Part of it can be text the server sent."""
    if isinstance(error, urllib.error.HTTPError):
        text = "HTTP %s %s" % (error.code, error.reason)
    elif isinstance(error, urllib.error.URLError):
        text = str(error.reason)
    elif isinstance(error, http.client.HTTPException):
        text = "the server's answer could not be read (%s)" % (str(error).strip() or error.__class__.__name__)
    elif isinstance(error, (EOFError, zlib.error)):
        text = "the compressed answer is damaged"
    else:
        text = str(error) or error.__class__.__name__
    text = " ".join(text.split())
    return text if len(text) <= 200 else text[:200] + "..."


def _megabytes(size):
    return "%d MB" % (size // (1024 * 1024))


class _WebRedirectsOnly(urllib.request.HTTPRedirectHandler):
    """urllib would also follow a redirect to an ftp: address. Only http and https are wanted here."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in ("http", "https"):
            raise urllib.error.HTTPError(newurl, code, "redirect to a link that is not http or https", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _expected_length(response):
    """The number of bytes the server announced, or None if it did not say."""
    if "chunked" in (response.headers.get("Transfer-Encoding") or "").lower():
        return None
    length = (response.headers.get("Content-Length") or "").strip()
    return int(length) if length.isdigit() else None


def _is_gzip(response):
    return (response.headers.get("Content-Encoding") or "").strip().lower() in ("gzip", "x-gzip")


def _read_page(response, limit):
    body = response.read(limit + 1)
    if len(body) > limit:
        raise FetchError("the answer is larger than %s" % _megabytes(limit))
    expected = _expected_length(response)
    if expected is not None and len(body) < expected:
        raise FetchError("the connection closed after %d of %d bytes" % (len(body), expected))
    if _is_gzip(response):
        unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
        body = unpacker.decompress(body, limit + 1)
        if len(body) > limit:
            raise FetchError("the answer unpacks to more than %s" % _megabytes(limit))
        if not unpacker.eof:
            raise FetchError("the compressed answer is cut off")
    return body


class Fetcher:
    """Fetches pages and keeps the cookies a site sets, the way one browser session would."""

    def __init__(self, user_agent=DEFAULT_USER_AGENT, timeout=60):
        self.user_agent = user_agent
        self.timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _WebRedirectsOnly()
        )

    def get(self, url, timeout=None):
        return self._open(url, None, timeout)

    def post(self, url, fields, timeout=None):
        """Send the fields the way a web form does."""
        return self._open(url, urllib.parse.urlencode(fields).encode("ascii"), timeout)

    def _open(self, url, data, timeout):
        try:
            request = urllib.request.Request(plain_ascii(url), data=data)
            if self.user_agent:
                request.add_header("User-Agent", self.user_agent)
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                body = _read_page(response, MAX_PAGE_BYTES)
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    text = body.decode(charset, errors="replace")
                except LookupError:
                    text = body.decode("utf-8", errors="replace")
                return Page(response.status, text, response.geturl())
        except urllib.error.HTTPError as error:
            reason, status = _reason(error), error.code
            error.close()
            raise FetchError(reason, status)
        except _REQUEST_ERRORS as error:
            raise FetchError(_reason(error))


def download(url, destination, user_agent=DEFAULT_USER_AGENT, verify_certificate=True, timeout=300,
             max_bytes=MAX_DOWNLOAD_BYTES):
    """Download one address to a file. Raises FetchError (and leaves no partial file) if it fails, if the
    server closes the connection before the whole file has arrived, or if the file is larger than max_bytes.

    Target servers often have expired or self-signed certificates, so the caller can switch the
    certificate check off for them.
    """
    # The working file's name starts with a dot, which the name of a downloaded file never does
    # (see links.safe_file_name), so it cannot be a file that was downloaded before
    folder, name = os.path.split(destination)
    partial = os.path.join(folder, ".part-" + name)
    try:
        url = plain_ascii(url.strip())
        if urllib.parse.urlsplit(url).scheme.lower() not in ("http", "https"):
            raise FetchError("not an http or https link")

        context = ssl.create_default_context()
        if not verify_certificate:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        request = urllib.request.Request(url)
        if user_agent:
            request.add_header("User-Agent", user_agent)
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _WebRedirectsOnly())

        with opener.open(request, timeout=timeout) as response:
            expected = _expected_length(response)
            if expected is not None and expected > max_bytes:
                raise FetchError("the file is larger than %s" % _megabytes(max_bytes))
            # A server may compress the file on the way although it was not asked to
            unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS) if _is_gzip(response) else None
            received = written = 0
            with open(partial, "wb") as target:
                while True:
                    block = response.read(1 << 16)
                    if not block:
                        break
                    received += len(block)
                    if unpacker is not None:
                        block = unpacker.decompress(block)
                    written += len(block)
                    if written > max_bytes:
                        raise FetchError("the file is larger than %s" % _megabytes(max_bytes))
                    target.write(block)
            if expected is not None and received < expected:
                raise FetchError("the connection closed after %d of %d bytes" % (received, expected))
            if unpacker is not None and not unpacker.eof:
                raise FetchError("the compressed file is cut off")
        os.replace(partial, destination)
    except urllib.error.HTTPError as error:
        reason, status = _reason(error), error.code
        error.close()
        _remove(partial)
        raise FetchError(reason, status)
    except _REQUEST_ERRORS as error:
        _remove(partial)
        raise FetchError(_reason(error))
    except BaseException:
        _remove(partial)
        raise


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
