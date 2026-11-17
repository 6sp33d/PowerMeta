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
        self.assertIn("Bing showed pages of other sites only for 'site:nasa.gov filetype:pdf': it has no pdf files of the site, or", log.text())
        self.assertEqual(len(server.requests), 1)

    def test_bing_is_first_asked_for_the_site_alone(self):
        # A search for "site:domain" has to return pages of that domain. Bing answers some searches
        # with pages that have nothing to do with them, and then the file searches would bring up
        # other sites only.
        stray = '<div class="b_ans">Did you mean <a href="https://www.legislature.mi.gov/">the legislature</a>?</div>'
        answers = {
            "keeps to the site": (200, fixture("bing_results.html")),
            "other sites only": (200, fixture("bing_unrelated.html")),
            # The site's own home page among other sites, and a link to the site that is not a result:
            # neither makes the answer one that keeps to the site
            "one of five": (200, bing_page(["https://www.legislature.mi.gov/", "http://a.example/", "http://b.example/", "http://c.example/", "http://d.example/"])),
            "a link that is not a result": (200, bing_page(["http://a.example/", "http://b.example/"]).replace("<ol", stray + "<ol")),
            "most of them": (200, bing_page(["https://www.legislature.mi.gov/", "https://legislature.mi.gov/a", "http://a.example/"])),
            "nothing": (200, bing_page([])),
            "a check page": (200, "<html><body><h1>One last step</h1><p>Please solve the challenge below</p></body></html>"),
            "error": (429, "slow down"),
        }
        expected = {
            "keeps to the site": (True, "to see whether it keeps to the site"),
            "other sites only": (False, "with pages of other sites (0 of its 9 results are on the site)"),
            "one of five": (False, "with pages of other sites (1 of its 5 results are on the site)"),
            "a link that is not a result": (False, "with pages of other sites (0 of its 2 results are on the site)"),
            "most of them": (True, "to see whether it keeps to the site"),
            "nothing": (False, "Bing returned no results for 'site:legislature.mi.gov': it has no pages of the site, or it did not answer"),
            "a check page": (False, "or it did not answer the search (a check page looks the same"),
            "error": (False, "Bing did not answer (HTTP 429"),
        }
        for name, answer in answers.items():
            with LocalServer(lambda request: answer) as server:
                with patched(sources, BING_URL=server.url + "/search"):
                    log = Messages()
                    known = sources.bing_knows_site(Fetcher(), "legislature.mi.gov", log)
            self.assertEqual(known, expected[name][0], name)
            self.assertIn(expected[name][1], log.text(), name)
            self.assertEqual([request.query for request in server.requests], [{"q": ["site:legislature.mi.gov"]}], name)

    def test_the_results_of_a_page_are_its_organic_results(self):
        page = fixture("bing_results.html")
        results = links.bing_results(page)
        self.assertEqual(len(results), links.count_bing_results(page))
        self.assertEqual(results[0], "https://legislature.mi.gov/Laws/MCL?objectName=mcl-257-636")
        self.assertEqual(links.bing_keeps_to_site(page, "legislature.mi.gov"), (10, 10, True))
        self.assertEqual(links.bing_keeps_to_site(fixture("bing_unrelated.html"), "nasa.gov"), (9, 0, False))
        self.assertEqual(links.bing_keeps_to_site("", "nasa.gov"), (0, 0, False))
        # The same with the reader that is used when the parser gives up
        with patched(links, _read_page=lambda page: None):
            self.assertEqual(links.bing_keeps_to_site(page, "legislature.mi.gov"), (10, 10, True))

    def test_one_session_is_kept_for_all_searches_of_an_engine(self):
        from powermeta import cli

        def respond(request):
            if request.path == "/cdx":
                return (200, "")
            return (200, bing_page(["https://example.com/"]), {"Set-Cookie": "MUID=abc123; Path=/"})

        with LocalServer(respond) as server:
            with patched(sources, BING_URL=server.url + "/search", ARCHIVE_INDEX_URL=server.url + "/cdx"):
                with patched(cli, log=lambda message="": None):
                    cli.find_files("example.com", ["pdf", "docx"], ["bing"], [], delay=0)
        cookies = [request.headers.get("Cookie") for request in server.requests]
        # The cookie Bing set with its first answer comes back with every later search
        self.assertEqual(cookies, [None, "MUID=abc123", "MUID=abc123"])

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
