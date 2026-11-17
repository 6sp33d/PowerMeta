"""The browser mode, run in a real Chrome through Selenium against a local stand-in for the search engines.

These tests never open Google, Bing or DuckDuckGo. They need Selenium and Chrome, so they only run when
asked for:

    POWERMETA_BROWSER_TESTS=1 python -m unittest tests.test_browser
"""

import os
import shutil
import tempfile
import unittest

from powermeta import browser, links

from .support import LocalServer, bing_page, duckduckgo_page, patched

try:
    import selenium  # noqa: F401

    HAVE_SELENIUM = True
except ImportError:
    HAVE_SELENIUM = False

WANTED = os.environ.get("POWERMETA_BROWSER_TESTS") == "1"

# The tests normally run the browser without a window. POWERMETA_BROWSER_TESTS_WINDOW=1 shows it.
ARGUMENTS = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
if os.environ.get("POWERMETA_BROWSER_TESTS_WINDOW") != "1":
    ARGUMENTS.append("--headless=new")


class PageStates(unittest.TestCase):
    """Telling results, an empty page and a check apart needs no browser."""

    def test_google(self):
        self.assertEqual(browser.page_state("google", "https://www.google.com/sorry/index?continue=x", ""), "check")
        self.assertEqual(browser.page_state("google", "https://consent.google.com/m?continue=x", ""), "check")
        self.assertEqual(browser.page_state("google", "https://www.google.com/search?q=x", '<form id="captcha-form">'), "check")
        self.assertEqual(browser.page_state("google", "https://www.google.com/search?q=x", '<button id="W0wltc">Reject all</button>'), "check")
        self.assertEqual(browser.page_state("google", "https://www.google.com/search?q=x", "<a href='/goto?url=T'>r</a>"), "results")

    def test_bing(self):
        self.assertEqual(browser.page_state("bing", "https://www.bing.com/search?q=x", bing_page(["https://example.com/a.pdf"])), "results")
        self.assertEqual(browser.page_state("bing", "https://www.bing.com/search?q=x", bing_page([])), "empty")
        self.assertEqual(browser.page_state("bing", "https://www.bing.com/turing/captcha/challenge", ""), "check")

    def test_duckduckgo(self):
        from .support import fixture

        self.assertEqual(browser.page_state("duckduckgo", "u", fixture("duckduckgo_bot_check.html")), "check")
        self.assertEqual(browser.page_state("duckduckgo", "u", duckduckgo_page(["https://example.com/a.pdf"])), "results")
        self.assertEqual(browser.page_state("duckduckgo", "u", duckduckgo_page([])), "empty")


class Unavailable(unittest.TestCase):
    def test_a_browser_that_cannot_start_is_reported_not_raised(self):
        messages = []

        def refuse(*args, **kwargs):
            raise browser.BrowserUnavailable("no browser here")

        with patched(browser, Session=refuse):
            self.assertEqual(browser.search(["google"], "example.com", ["pdf"], log=messages.append), [])
        self.assertIn("no browser here", "\n".join(messages))


@unittest.skipUnless(WANTED and HAVE_SELENIUM, "set POWERMETA_BROWSER_TESTS=1 (needs Selenium and Chrome)")
class InARealBrowser(unittest.TestCase):
    """The search engines are played by a local server on 127.0.0.1; the "target site" is the same server
    under the name localhost, so that result links lead to a different host, as they do in real life."""

    def setUp(self):
        self.profile = tempfile.mkdtemp(prefix="powermeta-browser-test-")
        self.addCleanup(shutil.rmtree, self.profile, ignore_errors=True)
        self.check_pending = {"google": False, "duckduckgo": False}
        self.questions = []
        self.messages = []

    # -- the stand-in ---------------------------------------------------------------------------------

    def respond(self, request):
        target = "http://localhost:%d" % self.server.port

        if request.path == "/search":  # Google
            if self.check_pending["google"]:
                return (200, '<html><title>check</title><body><form id="captcha-form">Our systems have detected unusual traffic from your computer network</form></body></html>')
            if request.query.get("start", ["0"])[0] != "0":
                return (200, "<html><body><a href='/search?q=x&start=0'>Previous</a></body></html>")
            return (
                200,
                "<html><title>results</title><body>"
                "<a href='/goto?url=T1'>a pdf behind a redirect</a>"
                "<a href='/goto?url=T2'>a docx behind a redirect, which a browser would download</a>"
                "<a href='/goto?url=T3'>a link the engine does not forward</a>"
                "<a href='/goto?url=T1'>the first one again</a>"
                "<a href='%s/files/direct.pdf'>a plain result link</a>"
                "<a href='/url?q=%s/files/wrapped.pdf&sa=U'>an old-style wrapped link</a>"
                "<a href='/search?q=x&start=10'>Next</a>"
                "<a href='http://other.example/elsewhere.pdf'>another site</a>"
                "</body></html>" % (target, urllib_quote(target)),
            )
        if request.path == "/goto":
            token = request.query["url"][0]
            if token == "T1":
                return (302, "", {"Location": target + "/files/hidden.pdf"})
            if token == "T2":
                return (302, "", {"Location": target + "/files/report.docx"})
            return (200, "<html><title>wait</title><body>Your request is being verified</body></html>")

        if request.path == "/bing":
            if request.query["first"][0] == "1":
                return (200, bing_page([target + "/files/b1.pdf", target + "/page.html", "http://other.example/x.pdf"]))
            return (200, bing_page([target + "/files/b2.pdf"]) if request.query["first"][0] == "4" else bing_page([]))

        if request.path == "/html/":  # DuckDuckGo
            if self.check_pending["duckduckgo"]:
                from .support import fixture

                return (202, fixture("duckduckgo_bot_check.html"))
            if request.method == "POST" and request.form.get("s") == "30":
                return (200, duckduckgo_page([target + "/files/d2.pdf"], previous_offset=0))
            wrapped = "//duckduckgo.com/l/?uddg=" + urllib_quote(target + "/files/d-wrapped.pdf") + "&amp;rut=abc"
            return (200, duckduckgo_page([target + "/files/d1.pdf", wrapped], next_offset=30, query="site:localhost filetype:pdf"))

        if request.path.endswith(".pdf"):
            return (200, b"%PDF-1.4\n%%EOF\n", {"Content-Type": "application/pdf"})
        if request.path.endswith(".docx"):
            return (200, b"PK\x03\x04", {"Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="report.docx"'})
        return (404, "not here")

    def answer(self, question):
        """Stands in for the person: "deals with" whatever check is showing, then presses Enter."""
        self.questions.append(question)
        for engine in self.check_pending:
            self.check_pending[engine] = False
        return ""

    def run_search(self, engines, file_types, max_pages=3):
        with LocalServer(self.respond) as server:
            self.server = server
            base = server.url
            with patched(
                browser,
                GOOGLE_URL=base + "/search",
                BING_URL=base + "/bing",
                DUCKDUCKGO_URL=base + "/html/",
                EXTRA_ARGUMENTS=ARGUMENTS,
                PROFILE_FOLDER=self.profile,
            ):
                found = browser.search(engines, "localhost", file_types, max_pages=max_pages, delay=0, log=self.messages.append, ask=self.answer)
            return found, list(server.requests)

    # -- the tests --------------------------------------------------------------------------------------

    def test_google_hidden_addresses_are_read_in_the_browser(self):
        found, requests = self.run_search(["google"], ["pdf", "docx"])
        target = "http://localhost:%d" % self.server.port
        self.assertEqual(
            sorted(found),
            [target + "/files/direct.pdf", target + "/files/hidden.pdf", target + "/files/report.docx", target + "/files/wrapped.pdf"],
        )
        log = "\n".join(self.messages)
        self.assertIn("Looking up the addresses behind 3 result links", log)
        self.assertIn("could not be read", log)
        self.assertEqual(self.questions, [])
        # Nothing was saved by the browser while it looked the addresses up
        self.assertEqual([name for name in os.listdir(os.getcwd()) if name.endswith((".docx", ".crdownload"))], [])

    def test_a_check_waits_for_the_person_and_then_carries_on(self):
        self.check_pending["google"] = True
        found, _ = self.run_search(["google"], ["pdf"])
        self.assertEqual(len(self.questions), 1)
        self.assertIn("Google is showing a check", self.questions[0])
        self.assertEqual(len(found), 3)

    def test_skipping_an_engine(self):
        self.check_pending["google"] = True
        self.answer = lambda question: "s"
        found, requests = self.run_search(["google", "bing"], ["pdf", "docx"])
        target = "http://localhost:%d" % self.server.port
        # Google was left alone after the first search; Bing was still asked
        self.assertEqual(sum(1 for request in requests if request.path == "/search"), 1)
        self.assertEqual(sorted(found), [target + "/files/b1.pdf", target + "/files/b2.pdf"])
        self.assertIn("[*] Google is skipped for the rest of this run.", self.messages)

    def test_bing_pages(self):
        found, requests = self.run_search(["bing"], ["pdf"])
        target = "http://localhost:%d" % self.server.port
        self.assertEqual(found, [target + "/files/b1.pdf", target + "/files/b2.pdf"])
        self.assertEqual([request.query["first"][0] for request in requests if request.path == "/bing"], ["1", "4", "5"])

    def test_duckduckgo_next_button_and_check(self):
        self.check_pending["duckduckgo"] = True
        found, requests = self.run_search(["duckduckgo"], ["pdf"])
        target = "http://localhost:%d" % self.server.port
        self.assertEqual(found, [target + "/files/d1.pdf", target + "/files/d-wrapped.pdf", target + "/files/d2.pdf"])
        self.assertEqual(len(self.questions), 1)
        posts = [request for request in requests if request.path == "/html/" and request.method == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].form.get("s"), "30")

    def test_the_browser_says_it_is_automated(self):
        with LocalServer(lambda request: (200, "<html><body>hello</body></html>")) as server:
            with patched(browser, EXTRA_ARGUMENTS=ARGUMENTS, PROFILE_FOLDER=self.profile):
                session = browser.Session(log=self.messages.append, ask=self.answer)
                try:
                    session._go(server.url + "/")
                    self.assertTrue(session.driver.execute_script("return navigator.webdriver"))
                finally:
                    session.close()


def urllib_quote(text):
    import urllib.parse

    return urllib.parse.quote(text, safe="")


if __name__ == "__main__":
    unittest.main()
