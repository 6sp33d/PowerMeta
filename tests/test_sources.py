import unittest

from powermeta import links, sources
from powermeta.web import Fetcher

from .support import LocalServer, RawServer, bing_page, duckduckgo_page, fixture, patched, raw_answer


class Messages(list):
    def __call__(self, message=""):
        self.append(message)

    def text(self):
        return "\n".join(self)


class Bing(unittest.TestCase):
    def test_reads_results_and_stops_when_a_page_adds_nothing(self):
        with LocalServer(lambda request: (200, fixture("bing_results.html"))) as server:
            with patched(sources, BING_URL=server.url + "/search"):
                log = Messages()
                found = sources.search_bing(Fetcher(), "legislature.mi.gov", "pdf", max_pages=5, delay=0, log=log)
        self.assertEqual(len(found), 3)
        self.assertIn("https://www.legislature.mi.gov/documents/mcl/pdf/mcl-chap257.pdf", found)
        # Page 1 held 10 results, so page 2 starts at result 11; it adds nothing and the search ends
        self.assertEqual([request.query["first"][0] for request in server.requests], ["1", "11"])
        self.assertEqual(server.requests[0].query["q"][0], "site:legislature.mi.gov filetype:pdf")
        self.assertIn("[*] Bing: 3 pdf files found", log)

    def test_says_so_when_no_result_is_on_the_domain(self):
        with LocalServer(lambda request: (200, fixture("bing_unrelated.html"))) as server:
            with patched(sources, BING_URL=server.url + "/search"):
                log = Messages()
                found = sources.search_bing(Fetcher(), "nasa.gov", "pdf", delay=0, log=log)
        self.assertEqual(found, [])
        self.assertIn("none of them are on nasa.gov", log.text())
        self.assertEqual(len(server.requests), 1)

    def test_follows_pages_up_to_the_limit(self):
        pages = {
            "1": bing_page(["https://example.com/a.pdf", "https://example.com/b.pdf"]),
            "3": bing_page(["https://example.com/c.pdf", "https://example.com/page.html"]),
            "5": bing_page(["https://example.com/d.pdf"]),
        }
        with LocalServer(lambda request: (200, pages.get(request.query["first"][0], bing_page([])))) as server:
            with patched(sources, BING_URL=server.url + "/search"):
                found = sources.search_bing(Fetcher(), "example.com", "pdf", max_pages=2, delay=0, log=Messages())
        self.assertEqual(found, ["https://example.com/a.pdf", "https://example.com/b.pdf", "https://example.com/c.pdf"])
        self.assertEqual(len(server.requests), 2)

    def test_an_error_is_reported_not_raised(self):
        with LocalServer(lambda request: (500, "broken")) as server:
            with patched(sources, BING_URL=server.url + "/search"):
                log = Messages()
                found = sources.search_bing(Fetcher(), "example.com", "pdf", delay=0, log=log)
        self.assertEqual(found, [])
        self.assertIn("Bing did not answer", log.text())


class DuckDuckGo(unittest.TestCase):
    def test_posts_the_query_and_follows_the_next_form(self):
        def respond(request):
            if request.form.get("s") == "30":
                return (200, duckduckgo_page(["https://example.com/c.pdf"], previous_offset=0))
            wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Ffiles.example.com%2Fb%2Epdf%3Fdl%3D1&amp;rut=abc"
            return (200, duckduckgo_page(["https://example.com/a.pdf", wrapped, "https://example.com/x.html"], next_offset=30))

        with LocalServer(respond) as server:
            with patched(sources, DUCKDUCKGO_URL=server.url + "/html/"):
                found, blocked = sources.search_duckduckgo(Fetcher(), "example.com", "pdf", delay=0, log=Messages())
        self.assertFalse(blocked)
        self.assertEqual(found, ["https://example.com/a.pdf", "https://files.example.com/b.pdf?dl=1", "https://example.com/c.pdf"])
        self.assertEqual([request.method for request in server.requests], ["POST", "POST"])
        self.assertEqual(server.requests[0].form, {"q": "site:example.com filetype:pdf", "b": ""})
        # The second request sends back what the "Next" form held, token included
        self.assertEqual(server.requests[1].form, {"q": "q", "s": "30", "vqd": "4-222&x"})

    def test_bot_check_blocks(self):
        with LocalServer(lambda request: (202, fixture("duckduckgo_bot_check.html"))) as server:
            with patched(sources, DUCKDUCKGO_URL=server.url + "/html/"):
                log = Messages()
                found, blocked = sources.search_duckduckgo(Fetcher(), "example.com", "pdf", delay=0, log=log)
        self.assertEqual((found, blocked), ([], True))
        self.assertIn("bot check", log.text())
        self.assertEqual(len(server.requests), 1)

    def test_refusal_blocks(self):
        with LocalServer(lambda request: (403, "Forbidden")) as server:
            with patched(sources, DUCKDUCKGO_URL=server.url + "/html/"):
                log = Messages()
                found, blocked = sources.search_duckduckgo(Fetcher(), "example.com", "pdf", delay=0, log=log)
        self.assertEqual((found, blocked), ([], True))
        self.assertIn("HTTP 403", log.text())

    def test_a_later_page_that_fails_is_reported_and_the_results_are_kept(self):
        def respond(request):
            if request.form.get("s") == "30":
                return (403, "Forbidden")
            return (200, duckduckgo_page(["https://example.com/a.pdf"], next_offset=30))

        with LocalServer(respond) as server:
            with patched(sources, DUCKDUCKGO_URL=server.url + "/html/"):
                log = Messages()
                found, blocked = sources.search_duckduckgo(Fetcher(), "example.com", "pdf", delay=0, log=log)
        self.assertEqual((found, blocked), (["https://example.com/a.pdf"], False))
        self.assertIn("did not answer for page 2 (HTTP 403 Forbidden)", log.text())

    def test_no_results_is_not_a_block(self):
        with LocalServer(lambda request: (200, duckduckgo_page([]))) as server:
            with patched(sources, DUCKDUCKGO_URL=server.url + "/html/"):
                found, blocked = sources.search_duckduckgo(Fetcher(), "example.com", "pdf", delay=0, log=Messages())
        self.assertEqual((found, blocked), ([], False))


class Archive(unittest.TestCase):
    ANSWER = (
        "http://www.example.com:80/docs/Report.pdf 20190102030405\n"
        "https://files.example.com/a/budget.xlsx 20210607080910\n"
        "https://www.example.com/about.html 20200101000000\n"
    )

    def test_one_request_with_the_documented_parameters(self):
        with LocalServer(lambda request: (200, self.ANSWER, {"Content-Type": "text/plain"})) as server:
            with patched(sources, ARCHIVE_INDEX_URL=server.url + "/cdx/search/cdx"):
                log = Messages()
                entries = sources.search_archive(Fetcher(), "example.com", ["pdf", "xlsx"], log=log)
        self.assertEqual([entry.link for entry in entries], ["http://www.example.com/docs/Report.pdf", "https://files.example.com/a/budget.xlsx"])
        self.assertEqual(len(server.requests), 1)
        query = server.requests[0].query
        self.assertEqual(server.requests[0].path, "/cdx/search/cdx")
        self.assertEqual(query["url"], ["example.com"])
        self.assertEqual(query["matchType"], ["domain"])
        self.assertEqual(query["fl"], ["original,timestamp"])
        self.assertEqual(query["collapse"], ["urlkey"])
        self.assertEqual(query["gzip"], ["false"])
        self.assertEqual(query["limit"], ["10000"])
        # Both filters arrive intact, "+" signs included
        self.assertEqual(query["filter"], ["statuscode:200", links.archive_filter(["pdf", "xlsx"])])
        self.assertIn("[*] Internet Archive: 2 files found", log)
        self.assertNotIn("incomplete", log.text())

    def test_says_so_when_the_answer_is_cut_off(self):
        with LocalServer(lambda request: (200, self.ANSWER)) as server:
            with patched(sources, ARCHIVE_INDEX_URL=server.url + "/cdx"):
                log = Messages()
                sources.search_archive(Fetcher(), "example.com", ["pdf", "xlsx"], max_results=3, log=log)
        self.assertIn("incomplete", log.text())

    def test_nothing_captured(self):
        with LocalServer(lambda request: (200, "")) as server:
            with patched(sources, ARCHIVE_INDEX_URL=server.url + "/cdx"):
                log = Messages()
                self.assertEqual(sources.search_archive(Fetcher(), "example.com", ["pdf"], log=log), [])
        self.assertIn("[*] Internet Archive: 0 files found", log)

    def test_an_error_is_reported_not_raised(self):
        with LocalServer(lambda request: (503, "busy")) as server:
            with patched(sources, ARCHIVE_INDEX_URL=server.url + "/cdx"):
                log = Messages()
                self.assertEqual(sources.search_archive(Fetcher(), "example.com", ["pdf"], log=log), [])
        self.assertIn("did not answer", log.text())

    def test_an_answer_that_is_cut_off_or_not_http_is_reported_not_raised(self):
        answers = {
            "/cut": b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\nhttp://example.com/a.pdf 2020",
            "/chunk": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1000\r\nhttp://example.com/a.pdf",
            "/garbage": b"NOT HTTP AT ALL\r\n\r\n",
            "/header": b"HTTP/1.1 200 OK\r\nX: " + b"a" * 70000 + b"\r\n\r\n",
            "/gzip": raw_answer(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"\xff" * 30, b"Content-Encoding: gzip\r\n"),
        }
        with RawServer(lambda path: answers[path.split("?")[0]]) as server:
            for path in answers:
                with patched(sources, ARCHIVE_INDEX_URL=server.url + path):
                    log = Messages()
                    self.assertEqual(sources.search_archive(Fetcher(timeout=10), "example.com", ["pdf"], log=log), [], path)
                self.assertIn("did not answer", log.text(), path)

    def test_gzip_answers_are_unpacked_within_a_limit(self):
        import gzip

        from powermeta import web

        packed = gzip.compress(b"http://example.com/a.pdf 20200101000000\n")
        with RawServer(lambda path: raw_answer(packed, b"Content-Encoding: gzip\r\n")) as server:
            self.assertEqual(Fetcher().get(server.url + "/x").text, "http://example.com/a.pdf 20200101000000\n")
            with patched(web, MAX_PAGE_BYTES=20), self.assertRaises(web.FetchError):
                Fetcher().get(server.url + "/x")
        bomb = gzip.compress(b"\0" * (2 * 1024 * 1024))
        with RawServer(lambda path: raw_answer(bomb, b"Content-Encoding: gzip\r\n")) as server:
            with patched(web, MAX_PAGE_BYTES=1024 * 1024), self.assertRaises(web.FetchError) as raised:
                Fetcher().get(server.url + "/x")
        self.assertIn("unpacks to more than", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
