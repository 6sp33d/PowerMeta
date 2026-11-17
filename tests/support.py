"""Helpers for the tests: a local web server that stands in for the search engines, the Internet
Archive and a target site, and a writer for small PDF files with known metadata.

Nothing in the tests talks to the real services.
"""

import base64
import contextlib
import http.server
import os
import socket
import threading
import urllib.parse

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as source:
        return source.read()


class Request:
    def __init__(self, method, path, query, form, headers=None):
        self.method = method
        self.path = path
        self.query = query  # {name: [values]}
        self.form = form  # {name: value} for a form post
        self.headers = headers or {}  # {name: value}


class LocalServer:
    """Serves whatever the given function returns for a request: (status, body) or (status, body, headers).

    Every request is recorded in .requests.
    """

    def __init__(self, respond):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self, method):
                parts = urllib.parse.urlsplit(self.path)
                form = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    posted = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                    form = {name: values[0] for name, values in posted.items()}
                request = Request(
                    method, parts.path, urllib.parse.parse_qs(parts.query, keep_blank_values=True), form, dict(self.headers.items())
                )
                outer.requests.append(request)
                answer = respond(request)
                status, body = answer[0], answer[1]
                headers = dict(answer[2]) if len(answer) > 2 else {}
                if isinstance(body, str):
                    body = body.encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", headers.pop("Content-Type", "text/html; charset=utf-8"))
                    self.send_header("Content-Length", str(len(body)))
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client did not wait for the answer

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class RawServer:
    """Sends exactly the bytes the given function returns for a request path, then closes the connection.
    For answers a well-behaved web server would never give. If the function returns an iterable of byte
    strings instead, they are sent one after the other until the client hangs up."""

    def __init__(self, respond):
        self._respond = respond
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(20)
        self.port = self._socket.getsockname()[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.paths = []

    def _accept(self):
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                block = connection.recv(65536)
                if not block:
                    break
                data += block
            path = data.split(b" ")[1].decode("latin-1") if data.count(b" ") > 1 else ""
            self.paths.append(path)
            answer = self._respond(path)
            for part in [answer] if isinstance(answer, bytes) else answer:
                connection.sendall(part)
        except OSError:
            pass
        finally:
            connection.close()

    def __enter__(self):
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._socket.close()


def raw_answer(body, extra_headers=b"", status=b"200 OK"):
    return b"HTTP/1.1 " + status + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n" + extra_headers + b"\r\n" + body


@contextlib.contextmanager
def patched(module, **values):
    """Temporarily replace module-level settings (such as the address of a search engine)."""
    saved = {name: getattr(module, name) for name in values}
    for name, value in values.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


def bing_wrapped(address):
    """Wrap an address the way Bing does in its result pages."""
    encoded = base64.urlsafe_b64encode(address.encode("utf-8")).decode("ascii").rstrip("=")
    return "https://www.bing.com/ck/a?!&amp;&amp;p=abc123&amp;ptn=3&amp;u=a1" + encoded + "&amp;ntb=1"


def bing_page(addresses):
    rows = "".join('<li class="b_algo"><h2><a href="%s">result</a></h2></li>\n' % bing_wrapped(a) for a in addresses)
    return '<html><body><ol id="b_results">\n%s</ol></body></html>' % rows


def duckduckgo_page(addresses, next_offset=None, previous_offset=None, query="q", token="4-222&amp;x"):
    rows = "".join(
        '<div class="result results_links web-result"><div class="links_main result__body">'
        '<h2 class="result__title"><a rel="nofollow" class="result__a" href="%s">result</a></h2></div></div>\n' % a
        for a in addresses
    )
    forms = ""
    for label, offset in (("Previous", previous_offset), ("Next", next_offset)):
        if offset is None:
            continue
        forms += (
            '<div class="nav-link"><form action="/html/" method="post"><input type="submit" value="%s" />'
            '<input type="hidden" name="q" value="%s" /><input type="hidden" name="s" value="%d" />'
            '<input type="hidden" name="vqd" value="%s" /></form></div>\n' % (label, query, offset, token)
        )
    return '<html><body><div id="links" class="results">\n%s%s</div></body></html>' % (rows, forms)


def write_pdf(path, author=None, creator=None):
    """Write a minimal one-page PDF whose document information holds the given Author and Creator."""
    info = "".join("/%s (%s) " % (name, value) for name, value in (("Author", author), ("Creator", creator)) if value)
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        "<< " + info + ">>",
    ]
    body = "%PDF-1.4\n"
    offsets = []
    for number, content in enumerate(objects, 1):
        offsets.append(len(body))
        body += "%d 0 obj\n%s\nendobj\n" % (number, content)
    xref = len(body)
    body += "xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += "".join("%010d 00000 n \n" % offset for offset in offsets)
    body += "trailer\n<< /Size %d /Root 1 0 R /Info 4 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    data = body.encode("ascii")
    if path is not None:
        with open(path, "wb") as target:
            target.write(data)
    return data
