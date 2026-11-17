"""Plain web requests: fetching pages (with cookies kept for the session) and downloading files."""

from __future__ import annotations

import gzip
import http.cookiejar
import os
import shutil
import ssl
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)


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
    if isinstance(error, urllib.error.HTTPError):
        return "HTTP %s %s" % (error.code, error.reason)
    if isinstance(error, urllib.error.URLError):
        return str(error.reason)
    return str(error) or error.__class__.__name__


class Fetcher:
    """Fetches pages and keeps the cookies a site sets, the way one browser session would."""

    def __init__(self, user_agent=DEFAULT_USER_AGENT, timeout=60):
        self.user_agent = user_agent
        self.timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def get(self, url, timeout=None):
        return self._open(url, None, timeout)

    def post(self, url, fields, timeout=None):
        """Send the fields the way a web form does."""
        return self._open(url, urllib.parse.urlencode(fields).encode("ascii"), timeout)

    def _open(self, url, data, timeout):
        request = urllib.request.Request(url, data=data)
        if self.user_agent:
            request.add_header("User-Agent", self.user_agent)
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                body = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    text = body.decode(charset, errors="replace")
                except LookupError:
                    text = body.decode("utf-8", errors="replace")
                return Page(response.status, text, response.geturl())
        except urllib.error.HTTPError as error:
            raise FetchError(_reason(error), error.code)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise FetchError(_reason(error))


def download(url, destination, user_agent=DEFAULT_USER_AGENT, verify_certificate=True, timeout=300):
    """Download one address to a file. Raises FetchError (and leaves no partial file) if it fails.

    Target servers often have expired or self-signed certificates, so the caller can switch the
    certificate check off for them.
    """
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise FetchError("not an http or https link")

    context = ssl.create_default_context()
    if not verify_certificate:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    request = urllib.request.Request(url)
    if user_agent:
        request.add_header("User-Agent", user_agent)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))

    partial = destination + ".part"
    try:
        with opener.open(request, timeout=timeout) as response, open(partial, "wb") as target:
            shutil.copyfileobj(response, target)
        os.replace(partial, destination)
    except (urllib.error.URLError, OSError, ValueError) as error:
        _remove(partial)
        raise FetchError(_reason(error), getattr(error, "code", 0) or 0)
    except BaseException:
        _remove(partial)
        raise


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
