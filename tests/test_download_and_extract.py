import contextlib
import io
import os
import shutil
import ssl
import stat
import subprocess
import tempfile
import threading
import unittest
import http.server

from powermeta import cli, exif, links, sources
from powermeta.web import FetchError, download

from .support import LocalServer, bing_page, patched, write_pdf

EXIFTOOL = exif.find_exiftool()


def run_cli(arguments):
    """Run the command line and return (exit code, everything it printed)."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli.main(arguments)
    return code, output.getvalue()


class TemporaryFolder(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="powermeta-test-")
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)


class Downloads(TemporaryFolder):
    def site(self, request):
        files = {
            "/files/report.pdf": write_pdf(None, "jsmith", "Writer"),
            "/other/report.pdf": write_pdf(None, "adoe", "Writer"),
            "/archive/20200101000000id_/http://site.example/gone.pdf": write_pdf(None, "archived", "Old Writer"),
        }
        if request.path in files:
            return (200, files[request.path], {"Content-Type": "application/pdf"})
        return (404, "not here")

    def run_downloads(self, file_links, archive_copies=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            counts = cli.download_files(file_links, self.folder, archive_copies=archive_copies)
        return counts, output.getvalue()

    def test_files_are_saved_and_a_repeated_name_gets_a_number(self):
        with LocalServer(self.site) as server:
            counts, output = self.run_downloads([server.url + "/files/report.pdf", server.url + "/other/report.pdf", "  ", ""])
        self.assertEqual(counts, (2, 0))
        self.assertEqual(sorted(os.listdir(self.folder)), ["report-1.pdf", "report.pdf"])
        self.assertIn("[*] Downloading 2 files now.", output)

    def test_a_missing_file_is_counted_and_leaves_nothing_behind(self):
        with LocalServer(self.site) as server:
            counts, output = self.run_downloads([server.url + "/gone.pdf", "ftp://example.com/a.pdf"])
        self.assertEqual(counts, (0, 2))
        self.assertEqual(os.listdir(self.folder), [])
        self.assertIn("HTTP 404", output)
        self.assertIn("not an http or https link", output)

    def test_the_archive_copy_is_fetched_when_the_site_no_longer_has_the_file(self):
        with LocalServer(self.site) as server:
            gone = server.url + "/gone.pdf"
            copies = {gone: server.url + "/archive/20200101000000id_/http://site.example/gone.pdf"}
            counts, output = self.run_downloads([gone, server.url + "/files/report.pdf"], copies)
            paths = [request.path for request in server.requests]
        self.assertEqual(counts, (2, 0))
        self.assertEqual(sorted(os.listdir(self.folder)), ["gone.pdf", "report.pdf"])
        self.assertIn("Trying the copy in the Internet Archive", output)
        # The copy was only asked for after the site said no, and not at all for the file that is still there
        self.assertEqual(paths.count("/archive/20200101000000id_/http://site.example/gone.pdf"), 1)
        with open(os.path.join(self.folder, "gone.pdf"), "rb") as saved:
            self.assertIn(b"archived", saved.read())

    def test_hostile_names_stay_inside_the_folder(self):
        with LocalServer(lambda request: (200, b"data")) as server:
            counts, _ = self.run_downloads([server.url + "/x/..%2F..%2Fescape.pdf", server.url + "/a.pdf/exiftool.exe", server.url + "/x/-o.pdf"])
        self.assertEqual(counts, (3, 0))
        self.assertEqual(sorted(os.listdir(self.folder)), ["_-o.pdf", "_..%2F..%2Fescape.pdf", "_exiftool.exe"])


@unittest.skipUnless(shutil.which("openssl"), "openssl is needed to make a test certificate")
class Certificates(TemporaryFolder):
    """Files from the target are fetched even from a server with a self-signed certificate; everything
    else (such as copies from the Internet Archive) is checked as usual."""

    def setUp(self):
        super().setUp()
        key, certificate = os.path.join(self.folder, "key.pem"), os.path.join(self.folder, "cert.pem")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", certificate, "-days", "1", "-subj", "/CN=localhost"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "4")
                self.end_headers()
                self.wfile.write(b"data")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "https://127.0.0.1:%d/a.pdf" % self.server.server_address[1]

    def test_self_signed_certificate(self):
        # A proxy configured for the test machine must not get between the test and its own server
        with patched(os, environ={k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}):
            target = os.path.join(self.folder, "a.pdf")
            with self.assertRaises(FetchError):
                download(self.url, target, verify_certificate=True, timeout=20)
            self.assertFalse(os.path.exists(target))
            download(self.url, target, verify_certificate=False, timeout=20)
            with open(target, "rb") as saved:
                self.assertEqual(saved.read(), b"data")


class ExifToolLookup(TemporaryFolder):
    def test_the_current_directory_is_never_searched(self):
        name = "exiftool.exe" if os.name == "nt" else "exiftool"
        planted = os.path.join(self.folder, name)
        with open(planted, "w") as target:
            target.write("#!/bin/sh\necho 99.99\n")
        os.chmod(planted, os.stat(planted).st_mode | stat.S_IXUSR)
        here = os.getcwd()
        os.chdir(self.folder)
        try:
            with patched(os, environ=dict(os.environ, PATH=os.pathsep.join(["", ".", "relative/bin"]))):
                self.assertIsNone(exif._on_path("exiftool"))
            with patched(os, environ=dict(os.environ, PATH=self.folder)):
                self.assertEqual(exif._on_path("exiftool"), planted)
        finally:
            os.chdir(here)

    @unittest.skipIf(os.name == "nt", "uses a shell script as a stand-in for an old ExifTool")
    def test_an_old_exiftool_is_refused(self):
        for reported, usable in (("10.47", False), ("12.23", False), ("12.24", True), ("13.64", True), ("nonsense", False)):
            fake = os.path.join(self.folder, "exiftool-" + reported)
            with open(fake, "w") as target:
                target.write("#!/bin/sh\necho %s\n" % reported)
            os.chmod(fake, 0o755)
            messages = []
            with patched(exif, find_exiftool=lambda: fake):
                self.assertEqual(exif.usable_exiftool(messages.append) is not None, usable, reported)
            if reported == "10.47":
                self.assertIn("CVE-2021-22204", "\n".join(messages))

    def test_missing_exiftool_is_explained(self):
        messages = []
        with patched(exif, find_exiftool=lambda: None):
            self.assertIsNone(exif.usable_exiftool(messages.append))
        self.assertIn("was not found", "\n".join(messages))


@unittest.skipUnless(EXIFTOOL, "ExifTool is not installed")
class Extraction(TemporaryFolder):
    def setUp(self):
        super().setUp()
        self.files = os.path.join(self.folder, "files with space")
        os.mkdir(self.files)
        write_pdf(os.path.join(self.files, "one.pdf"), "jsmith", "Writer, version 1")
        # A name that a shell would run as a command
        write_pdf(os.path.join(self.files, "two$(touch INJECTED);.pdf"), "adoe", "Writer, version 1")
        write_pdf(os.path.join(self.files, "no extension"), "12345", None)

    def test_authors_and_creators(self):
        count, values = exif.extract_authors(EXIFTOOL, self.files)
        self.assertEqual(count, 3)
        # A value with a comma stays in one piece, and a numeric author is kept as text
        self.assertEqual(values, ["12345", "adoe", "jsmith", "Writer, version 1"])
        self.assertFalse(os.path.exists("INJECTED"))
        self.assertEqual(sorted(os.listdir(self.files)), ["no extension", "one.pdf", "two$(touch INJECTED);.pdf"])

    def test_everything_to_csv(self):
        target = os.path.join(self.folder, "all.csv")
        self.assertEqual(exif.extract_all_to_csv(EXIFTOOL, self.files, target), 3)
        with open(target, "rb") as saved:
            data = saved.read()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"SourceFile", data.splitlines()[0])
        self.assertEqual(len(data.decode("utf-8-sig").strip().splitlines()), 4)

    def test_empty_folder(self):
        empty = os.path.join(self.folder, "empty")
        os.mkdir(empty)
        self.assertEqual(exif.extract_authors(EXIFTOOL, empty), (0, []))
        self.assertEqual(exif.extract_all_to_csv(EXIFTOOL, empty, os.path.join(self.folder, "none.csv")), 0)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "none.csv")))


class CommandLine(TemporaryFolder):
    """The whole run against a local stand-in: the target site is 127.0.0.1, and the same server answers
    as the Internet Archive's index, as Bing and as DuckDuckGo."""

    def respond(self, request):
        base = self.server.url
        if request.path == "/cdx":
            return (200, "%s/files/report.pdf 20200101000000\n%s/gone.pdf 20190101000000\n" % (base, base), {"Content-Type": "text/plain"})
        if request.path == "/bing":
            if request.query["q"][0].endswith("filetype:pdf") and request.query["first"][0] == "1":
                return (200, bing_page([base + "/files/report.pdf", base + "/files/second.pdf"]))
            return (200, bing_page([]))
        if request.path == "/ddg":
            return (403, "Forbidden")
        if request.path == "/files/report.pdf":
            return (200, write_pdf(None, "jsmith", "Writer"), {"Content-Type": "application/pdf"})
        if request.path == "/files/second.pdf":
            return (200, write_pdf(None, "adoe", "Writer"), {"Content-Type": "application/pdf"})
        if request.path.startswith("/web/20190101000000id_/"):
            return (200, write_pdf(None, "former.employee", "Old Writer"), {"Content-Type": "application/pdf"})
        return (404, "not here")

    def run_with_server(self, arguments):
        with LocalServer(self.respond) as server:
            self.server = server
            with patched(sources, ARCHIVE_INDEX_URL=server.url + "/cdx", BING_URL=server.url + "/bing", DUCKDUCKGO_URL=server.url + "/ddg"):
                with patched(links, ARCHIVE_COPY_PREFIX=server.url + "/web/"):
                    code, output = run_cli(arguments)
            return code, output, server

    def test_search_download_extract(self):
        out = os.path.join(self.folder, "out")
        listed = os.path.join(self.folder, "links.txt")
        code, output, server = self.run_with_server(
            ["-d", "127.0.0.1", "-t", "pdf, docx", "--delay", "0", "--download", "--extract", "-o", out, "--list-out", listed]
        )
        self.assertEqual(code, 0, output)
        self.assertIn("[*] Internet Archive: 2 files found", output)
        self.assertIn("[*] Bing: 2 pdf files found", output)
        self.assertIn("It is skipped for the rest of this run", output)
        self.assertIn("[*] A total of 3 files were discovered.", output)
        # DuckDuckGo refused once and was not asked again for the second file type
        self.assertEqual(sum(1 for request in server.requests if request.path == "/ddg"), 1)
        self.assertEqual(sum(1 for request in server.requests if request.path == "/bing"), 3)
        self.assertEqual(sum(1 for request in server.requests if request.path == "/cdx"), 1)
        self.assertEqual(sorted(os.listdir(out)), ["gone.pdf", "report.pdf", "second.pdf"])
        self.assertIn("Trying the copy in the Internet Archive", output)
        self.assertIn("[*] Downloaded 3 files", output)
        with open(listed, encoding="ascii") as saved:
            self.assertEqual(len(saved.read().split()), 3)
        if EXIFTOOL:
            for author in ("adoe", "former.employee", "jsmith", "Old Writer", "Writer"):
                self.assertIn("\n" + author + "\n", output)

    def test_without_a_terminal_nothing_is_downloaded_unless_asked(self):
        out = os.path.join(self.folder, "out")
        with patched(cli.sys, stdin=io.StringIO("")):
            code, output, _ = self.run_with_server(["-d", "http://127.0.0.1/", "-t", "pdf", "--sources", "archive", "-o", out])
        self.assertEqual(code, 0, output)
        self.assertIn("[*] A total of 2 files were discovered.", output)
        self.assertIn("No files will be downloaded", output)
        self.assertFalse(os.path.exists(out))

    def test_list_in_skips_searching(self):
        listed = os.path.join(self.folder, "links.txt")
        out = os.path.join(self.folder, "out")
        with LocalServer(self.respond) as server:
            self.server = server
            with open(listed, "w", encoding="utf-8-sig") as target:
                target.write(server.url + "/files/report.pdf\n\n" + server.url + "/gone.pdf\n")
            code, output = run_cli(["-d", "127.0.0.1", "--list-in", listed, "--download", "-o", out])
            paths = [request.path for request in server.requests]
        self.assertEqual(code, 0, output)
        self.assertIn("[*] A total of 2 files were found.", output)
        self.assertEqual(paths, ["/files/report.pdf", "/gone.pdf"])
        self.assertIn("[*] Downloaded 1 files", output)

    def test_engines_searched_in_the_browser_are_not_also_asked_with_plain_requests(self):
        from powermeta import browser

        calls = []

        def fake_search(engines, domain, file_types, max_pages, delay, browser_name, log):
            calls.append((list(engines), domain, list(file_types), browser_name))
            # The browser finds today's https address of a file the Archive lists under http
            return ["https://127.0.0.1:%d/files/report.pdf" % self.server.port, "http://127.0.0.1:%d/files/browser-only.pdf" % self.server.port]

        with patched(browser, search=fake_search):
            code, output, server = self.run_with_server(
                ["-d", "127.0.0.1", "-t", "pdf", "--delay", "0", "--browser", "google,bing", "--browser-name", "edge", "--list-out", os.path.join(self.folder, "l.txt")]
            )
        self.assertEqual(code, 0, output)
        self.assertEqual(calls, [(["google", "bing"], "127.0.0.1", ["pdf"], "edge")])
        paths = [request.path for request in server.requests]
        # Bing went through the browser, so only DuckDuckGo and the Archive were asked with plain requests
        self.assertNotIn("/bing", paths)
        self.assertIn("/ddg", paths)
        self.assertIn("/cdx", paths)
        with open(os.path.join(self.folder, "l.txt")) as saved:
            listed = saved.read().split()
        # report.pdf is listed once, under the address the browser found; gone.pdf comes from the Archive
        self.assertEqual(
            listed,
            sorted(["https://127.0.0.1:%d/files/report.pdf" % server.port, "http://127.0.0.1:%d/files/browser-only.pdf" % server.port, "http://127.0.0.1:%d/gone.pdf" % server.port]),
        )

    def test_bad_input_is_explained(self):
        self.assertEqual(run_cli(["-d", "not a domain"])[0], 1)
        self.assertIn("No file types", run_cli(["-d", "example.com", "-t", "p df"])[1])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["-d", "example.com", "--sources", "altavista"])

    @unittest.skipUnless(EXIFTOOL, "ExifTool is not installed")
    def test_extract_dir(self):
        write_pdf(os.path.join(self.folder, "one.pdf"), "jsmith", "Writer")
        target = os.path.join(self.folder, "all.csv")
        code, output = run_cli(["--extract-dir", self.folder, "--csv", target])
        self.assertEqual(code, 0, output)
        self.assertIn("Metadata for 1 files was written", output)
        code, output = run_cli(["--extract-dir", os.path.join(self.folder, "missing")])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
