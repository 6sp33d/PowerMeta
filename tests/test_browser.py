"""The browser mode, run in a real Chrome through Selenium against a local stand-in for the search engines.

These tests never open Google, Bing or DuckDuckGo. They need Selenium and Chrome, so they only run when
asked for:

    POWERMETA_BROWSER_TESTS=1 python -m unittest tests.test_browser
"""

import os
import shutil
import tempfile
import time
import unittest
import urllib.parse

from powermeta import browser, web

from .support import LocalServer, bing_page, duckduckgo_page, fixture, patched

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

SEARCH_BOX = "<form action='/search'><input name='q' value='site:localhost filetype:pdf'></form>"
CHECK_PAGE = (
    '<html><title>check</title><body><form id="captcha-form">Our systems have detected unusual traffic '
    "from your computer network</form></body></html>"
)
CONTINUE_PAGE = "<html><title>one moment</title><body>Verifying your request <button>Continue</button></body></html>"


def quote(text):
    return urllib.parse.quote(text, safe="")


class PageStates(unittest.TestCase):
    """Telling results, an empty page and a check apart needs no browser."""

    def test_google(self):
        self.assertEqual(browser.page_state("google", "https://www.google.com/sorry/index?continue=x", ""), "check")
        self.assertEqual(browser.page_state("google", "https://consent.google.com/m?continue=x", ""), "check")
        self.assertEqual(browser.page_state("google", "https://www.google.com/search?q=x", '<form id="captcha-form">'), "check")
        self.assertEqual(browser.page_state("google", "https://www.google.com/search?q=x", '<button id="W0wltc">Reject all</button>'), "check")
        self.assertEqual(browser.page_state("google", "https://www.google.com/search?q=x", '<button class="x" id="L2AGLb">'), "check")
        self.assertEqual(browser.page_state("google", "https://www.google.com/search?q=x", "<a href='/goto?url=T'>r</a>"), "results")
        self.assertEqual(browser.page_state("google", "about:blank", ""), "results")
        self.assertEqual(browser.page_state("google", None, None), "results")

    def test_words_in_a_result_do_not_make_a_results_page_a_check(self):
        url = "https://www.google.com/search?q=site%3Aexample.com+filetype%3Apdf"
        pages = [
            '<html><body><a href="https://example.com/a.pdf">Why Google says: unusual traffic from your computer network (PDF)</a></body></html>',
            '<html><body><span>look for id="captcha-form" or id="L2AGLb" in the page</span></body></html>',
            "<html><body><script>var form = '<form id=\"captcha-form\">';</script><a href='https://example.com/a.pdf'>a</a></body></html>",
        ]
        for page in pages:
            self.assertEqual(browser.page_state("google", url, page), "results", page)
        # Only the path of the address counts, not a search for the same words
        self.assertEqual(browser.page_state("google", "https://www.google.com/search?q=/sorry/+consent.example", ""), "results")

    def test_a_results_page_has_the_search_box(self):
        self.assertTrue(browser.has_search_box('<form action="/search"><textarea class="gLFyf" name="q" rows="1"></textarea></form>'))
        self.assertTrue(browser.has_search_box(SEARCH_BOX))
        self.assertFalse(browser.has_search_box(CONTINUE_PAGE))
        self.assertFalse(browser.has_search_box(CHECK_PAGE))
        self.assertFalse(browser.has_search_box("<script>var box = '<input name=\"q\">';</script><p>name=\"q\"</p>"))
        self.assertFalse(browser.has_search_box(None))

    def test_bing(self):
        self.assertEqual(browser.page_state("bing", "https://www.bing.com/search?q=x", bing_page(["https://example.com/a.pdf"])), "results")
        self.assertEqual(browser.page_state("bing", "https://www.bing.com/search?q=x", bing_page([])), "empty")
        self.assertEqual(browser.page_state("bing", "https://www.bing.com/turing/captcha/challenge", ""), "check")

    def test_duckduckgo(self):
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

    @unittest.skipUnless(HAVE_SELENIUM, "needs Selenium")
    def test_an_old_selenium_is_named_as_the_problem(self):
        messages = []
        with patched(selenium, __version__="3.141.0"):
            self.assertEqual(browser.search(["google"], "example.com", ["pdf"], log=messages.append), [])
        self.assertIn("needs Selenium 4 or newer (this is Selenium 3.141.0)", "\n".join(messages))

    @unittest.skipUnless(HAVE_SELENIUM, "needs Selenium")
    def test_a_profile_folder_that_cannot_be_made_is_reported_not_raised(self):
        folder = tempfile.mkdtemp(prefix="powermeta-browser-test-")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        blocker = os.path.join(folder, "a file")
        open(blocker, "w").close()
        messages = []
        with patched(browser, PROFILE_FOLDER=os.path.join(blocker, "browser-chrome")):
            self.assertEqual(browser.search(["bing"], "example.com", ["pdf"], log=messages.append), [])
        self.assertIn("cannot be used", "\n".join(messages))


@unittest.skipUnless(WANTED and HAVE_SELENIUM, "set POWERMETA_BROWSER_TESTS=1 (needs Selenium and Chrome)")
class InARealBrowser(unittest.TestCase):
    """The search engines are played by a local server on 127.0.0.1; the "target site" is the same server
    under the name localhost, so that result links lead to a different host, as they do in real life."""

    def setUp(self):
        self.profile = tempfile.mkdtemp(prefix="powermeta-browser-test-")
        self.addCleanup(shutil.rmtree, self.profile, ignore_errors=True)
        # What the stand-in puts in the way until the "person" has dealt with it
        self.check_pending = {"google": False, "duckduckgo": False, "link": False, "continue": False}
        self.checks_clear = True  # False: a check stays, whatever the person does
        self.google_links = ["T1", "T2", "T1", "direct", "wrapped"]  # what Google's first page links to
        self.plain_requests_refused = False  # True: Google's links answer programs with a page, not a redirect
        self.bing_site_answer = "site"  # what Bing shows for "site:localhost": "site", "others" or "nothing"
        self.duckduckgo_extras = ""  # more markup for DuckDuckGo's first page
        self.questions = []
        self.messages = []
        self.sessions = []

    # -- the stand-in ---------------------------------------------------------------------------------

    def google_page(self):
        target = self.target
        kinds = {
            "direct": "<p><a href='%s/files/direct.pdf'><h3>a plain result link</h3></a></p>" % target,
            "wrapped": "<p><a href='/url?q=%s/files/wrapped.pdf&sa=U'><h3>an old-style wrapped link</h3></a></p>" % quote(target),
        }
        rows = "".join(
            kinds.get(kind, "<p><a href='/goto?url=%s' ping='/ping'><h3>a result that hides its address</h3></a></p>" % kind)
            for kind in self.google_links
        )
        return (
            "<html><title>results</title><body>" + SEARCH_BOX + rows + "<a href='/search?q=x&start=10'>Next</a>"
            "<a href='http://other.example/elsewhere.pdf'>another site</a></body></html>"
        )

    def respond(self, request):
        target = self.target

        if request.path == "/search":  # Google
            if self.check_pending["google"]:
                return (200, CHECK_PAGE)
            if self.check_pending["continue"]:
                return (200, CONTINUE_PAGE)
            if request.query.get("start", ["0"])[0] != "0":
                return (200, "<html><body>" + SEARCH_BOX + "<a href='/search?q=x&start=0'>Previous</a></body></html>")
            return (200, self.google_page())
        if request.path == "/ping":
            return (204, "")
        if request.path == "/goto":
            token = request.query["url"][0]
            by_program = "PowerMeta" in request.headers.get("User-Agent", "") or "Example" in request.headers.get("User-Agent", "")
            if by_program and self.plain_requests_refused:
                return (200, CONTINUE_PAGE)
            if self.check_pending["link"]:
                return (302, "", {"Location": "/sorry/index?continue=" + quote("/goto?url=" + token)})
            destinations = {
                "T1": "/files/hidden.pdf",
                "T2": "/files/report.docx",
                "moved": "/files/moved.pdf",
                "frag": "/files/frag.pdf",
                "page": "/files/a-page.pdf",
                "popup": "/popup.html",
                "alert": "/alert.html",
                "alerts": "/alerts.html",
                "alerts-forever": "/alerts-forever.html",
                "stay": "/stay.html",
                "slow": "/files/slow.pdf",
            }
            if token in destinations:
                return (302, "", {"Location": target + destinations[token]})
            if token == "two-steps":
                return (302, "", {"Location": "/goto?url=T1"})
            if token == "meta":
                return (200, "<html><head><meta http-equiv='refresh' content='0;url=%s/files/meta.pdf'></head></html>" % target)
            if token == "notice":
                return (
                    200,
                    "<html><body>You are being sent to <a href='%s/files/noticed.pdf'>this address</a>. "
                    "<a href='/search?q=x'>Back to the results</a></body></html>" % target,
                )
            if token == "continue":
                # Wants its button pressed, which the "person" does; then it sends the browser on
                if self.check_pending["continue-link"]:
                    return (200, CONTINUE_PAGE)
                return (302, "", {"Location": target + "/files/continued.pdf"})
            # Any other link shows a page and leads nowhere
            return (200, "<html><title>wait</title><body>Your request is being verified</body></html>")
        if request.path == "/sorry/index":
            return (200, CHECK_PAGE)

        if request.path == "/bing":
            if "first" not in request.query:  # the search for the site alone
                if self.bing_site_answer == "site":
                    return (200, bing_page([target + "/", target + "/about.html", "http://other.example/"]))
                return (200, bing_page(["http://other.example/", "http://unrelated.example/x.pdf"] if self.bing_site_answer == "others" else []))
            if request.query["first"][0] == "1":
                return (200, bing_page([target + "/files/b1.pdf", target + "/page.html", "http://other.example/x.pdf"]))
            return (200, bing_page([target + "/files/b2.pdf"]) if request.query["first"][0] == "4" else bing_page([]))

        if request.path == "/html/":  # DuckDuckGo
            if self.check_pending["duckduckgo"] and (request.method == "POST" or self.check_pending["duckduckgo"] != "later pages"):
                return (202, fixture("duckduckgo_bot_check.html"))
            if request.method == "POST" and request.form.get("s") == "30":
                return (200, duckduckgo_page([target + "/files/d2.pdf"], previous_offset=0))
            wrapped = "//duckduckgo.com/l/?uddg=" + quote(target + "/files/d-wrapped.pdf") + "&amp;rut=abc"
            page = duckduckgo_page([target + "/files/d1.pdf", wrapped], next_offset=30, query="site:localhost filetype:pdf")
            return (200, page.replace("</body>", self.duckduckgo_extras + "</body>"))

        if request.path == "/files/moved.pdf":
            return (302, "", {"Location": target + "/viewer?id=7"})
        if request.path == "/files/a-page.pdf":
            return (200, "<html><body>a page under the name of a file</body></html>")
        if request.path == "/files/slow.pdf":
            time.sleep(6)
        if request.path == "/popup.html":
            return (200, "<html><body><script>window.open('/blank.html');window.open('/blank.html','_blank','width=200,height=200');</script></body></html>")
        if request.path == "/alert.html":
            return (200, "<html><body><script>alert('hello')</script>after</body></html>")
        if request.path in ("/alerts.html", "/alerts-forever.html"):
            count = 20 if request.path == "/alerts.html" else 1000000
            return (200, "<html><body><script>for (var i = 0; i < %d; i++) { alert('again ' + i) }</script></body></html>" % count)
        if request.path == "/stay.html":
            return (
                200,
                "<html><body><script>history.pushState({}, '', '/stay-1'); history.pushState({}, '', '/stay-2');"
                "window.onpopstate = function () { history.pushState({}, '', '/stay-again'); };</script>held</body></html>",
            )
        if request.path.endswith(".pdf"):
            return (200, b"%PDF-1.4\n%%EOF\n", {"Content-Type": "application/pdf"})
        if request.path.endswith(".docx"):
            return (200, b"PK\x03\x04", {"Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="report.docx"'})
        return (200, "<html><body>a page</body></html>")

    def answer(self, question):
        """Stands in for the person: "deals with" whatever is in the way, then presses Enter."""
        self.questions.append(question)
        if self.checks_clear:
            for name in self.check_pending:
                self.check_pending[name] = False
        return ""

    @property
    def target(self):
        return "http://localhost:%d" % self.server.port

    def started(self, server):
        """Point the browser module at the stand-in. Returns the context manager that undoes it."""
        self.server = server
        self.check_pending.setdefault("continue-link", False)
        outer = self

        class Recorded(browser.Session):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                outer.sessions.append(self)

        return patched(
            browser,
            GOOGLE_URL=server.url + "/search",
            BING_URL=server.url + "/bing",
            DUCKDUCKGO_URL=server.url + "/html/",
            EXTRA_ARGUMENTS=ARGUMENTS,
            PROFILE_FOLDER=self.profile,
            LOOKUP_WAIT=2,
            SHOWN_WAIT=0.5,
            Session=Recorded,
        )

    def run_search(self, engines, file_types, max_pages=3, **more):
        with LocalServer(self.respond) as server:
            with self.started(server):
                found = browser.search(
                    engines, "localhost", file_types, max_pages=max_pages, delay=0, log=self.messages.append,
                    ask=lambda question: self.answer(question), **more
                )
            return found, list(server.requests)

    def log(self):
        return "\n".join(self.messages)

    def lookups(self, requests):
        """The result links that were asked for, in order."""
        return [request.query["url"][0] for request in requests if request.path == "/goto"]

    # -- Google -----------------------------------------------------------------------------------------

    def test_google_results_are_clicked_to_read_their_addresses(self):
        found, requests = self.run_search(["google"], ["pdf", "docx"])
        target = self.target
        self.assertEqual(
            sorted(found),
            [target + "/files/direct.pdf", target + "/files/hidden.pdf", target + "/files/report.docx", target + "/files/wrapped.pdf"],
        )
        self.assertIn("Reading where 2 results lead (each is clicked in the browser window)", self.log())
        self.assertNotIn("was not read", self.log())
        self.assertEqual(self.questions, [])
        # Each result was asked for once in the whole run, although it is listed twice and for both searches
        self.assertEqual(self.lookups(requests), ["T1", "T2"])
        # The search engine saw a click on its results page, not an address opened out of nowhere
        for request in requests:
            if request.path == "/goto":
                self.assertIn("/search?", request.headers.get("Referer", ""))
                self.assertEqual(request.headers.get("Sec-Fetch-Site"), "same-origin")
        self.assertEqual(sum(1 for request in requests if request.path == "/ping"), 2)
        # The results page was loaded once per page and search, and never again for a lookup
        self.assertEqual(sum(1 for request in requests if request.path == "/search"), 4)
        # Nothing was saved by the browser while it read the addresses
        self.assertEqual([name for name in os.listdir(os.getcwd()) if name.endswith((".docx", ".crdownload"))], [])

    def test_the_address_the_search_engine_listed_is_kept_wherever_the_site_sends_the_browser_next(self):
        self.google_links = ["moved", "frag#section", "two-steps", "meta", "notice", "page"]
        found, requests = self.run_search(["google"], ["pdf"])
        target = self.target
        self.assertEqual(
            sorted(found),
            sorted(
                target + path
                for path in ("/files/moved.pdf", "/files/frag.pdf", "/files/hidden.pdf", "/files/meta.pdf", "/files/noticed.pdf", "/files/a-page.pdf")
            ),
        )
        self.assertEqual(self.questions, [])
        # Pages that took over the window were left by going back: the search was not asked again
        self.assertEqual(sum(1 for request in requests if request.path == "/search"), 2)
        self.assertEqual(len(self.lookups(requests)), 7)  # one per result, and the second step of "two-steps"

    def test_a_page_of_the_search_engine_in_the_way_waits_for_the_person(self):
        # The result link shows a page with a button first. Nobody is hurried: the question comes when
        # the page is there, and the address is read once the person has dealt with it.
        self.google_links = ["continue", "T1"]
        self.check_pending["continue-link"] = True

        def press_continue(question):
            self.questions.append(question)
            self.check_pending["continue-link"] = False
            # What pressing the button does: the page asks for the link again
            self.sessions[-1].driver.execute_script("location.reload()")
            time.sleep(1)
            return ""

        self.answer = press_continue
        found, _ = self.run_search(["google"], ["pdf"])
        self.assertEqual(sorted(found), [self.target + "/files/continued.pdf", self.target + "/files/hidden.pdf"])
        self.assertEqual(len(self.questions), 1)
        self.assertIn("a page of its own instead of the result", self.questions[0])
        self.assertIn("Type n and Enter to stop", self.questions[0])

    def test_typing_n_stops_the_lookups_and_keeps_the_rest(self):
        self.google_links = ["dead-1", "T1", "direct"]
        self.answer = lambda question: self.questions.append(question) or "n"
        found, requests = self.run_search(["google", "bing"], ["pdf", "docx"])
        target = self.target
        self.assertEqual(sorted(found), [target + "/files/b1.pdf", target + "/files/b2.pdf", target + "/files/direct.pdf"])
        self.assertEqual(len(self.questions), 1)
        self.assertEqual(self.lookups(requests), ["dead-1"])
        self.assertIn("[*] Results that hide their address are left out for the rest of this run.", self.messages)
        self.assertIn("The address behind 2 Google results was not read.", self.log())

    def test_a_link_that_leads_nowhere_is_asked_about_and_counted(self):
        self.google_links = ["T1", "dead-end"]
        started = time.time()
        found, requests = self.run_search(["google"], ["pdf", "docx"])
        self.assertEqual(found, [self.target + "/files/hidden.pdf"])
        self.assertEqual(len(self.questions), 2)  # once for each search that listed it
        self.assertEqual(self.log().count("The address behind 1 Google results was not read."), 2)
        self.assertEqual(self.lookups(requests), ["T1", "dead-end", "dead-end"])
        self.assertLess(time.time() - started, 60)

    def test_lookups_stop_when_no_link_shows_where_it_leads(self):
        self.google_links = ["dead-1", "dead-2", "dead-3", "dead-4", "dead-5", "direct"]
        found, requests = self.run_search(["google"], ["pdf", "docx"])
        self.assertEqual(found, [self.target + "/files/direct.pdf"])
        self.assertIn("Three Google results in a row did not show where they lead", self.log())
        self.assertEqual(self.lookups(requests), ["dead-1", "dead-2", "dead-3"])
        self.assertEqual(len(self.questions), 3)
        self.assertIn("The address behind 5 Google results was not read.", self.log())

    def test_a_slow_file_does_not_hold_the_lookup_up(self):
        self.google_links = ["slow", "T1"]
        started = time.time()
        found, _ = self.run_search(["google"], ["pdf"])
        self.assertEqual(sorted(found), [self.target + "/files/hidden.pdf", self.target + "/files/slow.pdf"])
        self.assertEqual(self.questions, [])
        self.assertLess(time.time() - started, 30)

    def test_pages_that_hold_on_to_the_window_do_not_end_the_search(self):
        # Results that lead to pages which open windows, put up dialogs without end, or do not let the
        # browser go back. The search carries on with the results after them.
        self.google_links = ["popup", "alert", "alerts", "stay", "T1"]
        found, requests = self.run_search(["google"], ["pdf", "docx"])
        self.assertEqual(sorted(found), [self.target + "/files/hidden.pdf"])
        self.assertFalse(self.sessions[-1].gone)
        self.assertNotIn("stops here", self.log())
        self.assertNotIn("stopped early", self.log())
        self.assertEqual(self.questions, [])
        self.assertEqual(sorted(set(self.lookups(requests))), ["T1", "alert", "alerts", "popup", "stay"])

    def test_a_page_with_dialogs_without_end_ends_the_browser_search_and_nothing_else(self):
        # Such a page takes every command sent to its window. What was found before is kept.
        self.google_links = ["direct", "T1", "alerts-forever", "T2"]
        started = time.time()
        found, _ = self.run_search(["bing", "google", "duckduckgo"], ["pdf"])
        target = self.target
        self.assertEqual(
            sorted(found), [target + "/files/b1.pdf", target + "/files/b2.pdf", target + "/files/direct.pdf", target + "/files/hidden.pdf"]
        )
        self.assertEqual(sum(1 for message in self.messages if "The browser search stops here" in message), 1)
        self.assertNotIn("stopped early", self.log())
        self.assertLess(time.time() - started, 90)

    def test_an_address_opened_in_a_second_tab_leaves_nothing_open(self):
        # What is done for a result that cannot be clicked
        with LocalServer(self.respond) as server:
            with self.started(server):
                session = browser.Session(log=self.messages.append, ask=self.answer)
                try:
                    session._go(server.url + "/search?q=x")
                    for token, page in (
                        ("popup", "/popup.html"), ("alert", "/alert.html"), ("alerts-forever", "/alerts-forever.html"), ("T1", "/files/hidden.pdf"),
                    ):
                        self.assertEqual(session.resolve(server.url + "/goto?url=" + token), self.target + page)
                        self.assertEqual(len(session.driver.window_handles), 1, token)
                        self.assertIn("/search", session.url())
                    self.assertFalse(session.gone)
                finally:
                    session.close()

    def test_addresses_can_be_read_with_plain_requests_instead(self):
        found, requests = self.run_search(["google"], ["pdf", "docx"], lookup="request")
        target = self.target
        self.assertEqual(
            sorted(found),
            [target + "/files/direct.pdf", target + "/files/hidden.pdf", target + "/files/report.docx", target + "/files/wrapped.pdf"],
        )
        self.assertIn("Reading where 2 results lead (with plain requests to Google's links)", self.log())
        asked = [request for request in requests if request.path == "/goto"]
        self.assertEqual([request.query["url"][0] for request in asked], ["T1", "T2"])
        # The requests say who they come from, and the files themselves were not asked for
        self.assertEqual({request.headers.get("User-Agent") for request in asked}, {web.PROGRAM_USER_AGENT})
        self.assertEqual([request.path for request in requests if request.path.startswith("/files/")], [])

    def test_results_are_clicked_when_plain_requests_get_no_answer(self):
        self.plain_requests_refused = True
        found, requests = self.run_search(["google"], ["pdf", "docx"], lookup="request", user_agent="Example/1.0")
        self.assertEqual(len(found), 4)
        self.assertIn("Google does not tell plain requests where its result links lead (HTTP 200 and no redirect)", self.log())
        agents = [request.headers.get("User-Agent") for request in requests if request.path == "/goto"]
        # Two requests were turned away; after that, and for those two, the results were clicked
        self.assertEqual(agents.count("Example/1.0"), 2)
        self.assertEqual(len(agents), 4)

    def test_lookups_can_be_switched_off(self):
        found, requests = self.run_search(["google"], ["pdf"], lookup="off")
        self.assertEqual(sorted(found), [self.target + "/files/direct.pdf", self.target + "/files/wrapped.pdf"])
        self.assertEqual(self.lookups(requests), [])
        self.assertIn("The address behind 2 Google results was not read.", self.log())

    def test_a_check_waits_for_the_person_and_then_carries_on(self):
        self.check_pending["google"] = True
        found, _ = self.run_search(["google"], ["pdf"])
        self.assertEqual(len(self.questions), 1)
        self.assertIn("Google is showing a check", self.questions[0])
        self.assertEqual(len(found), 3)

    def test_a_page_that_is_not_a_results_page_waits_for_the_person(self):
        # Something this program does not know: no results and no search box, just a button
        self.check_pending["continue"] = True
        found, _ = self.run_search(["google"], ["pdf"])
        self.assertEqual(len(self.questions), 1)
        self.assertIn("something else than results", self.questions[0])
        self.assertEqual(len(found), 3)

    def test_a_check_on_a_result_link_waits_for_the_person_too(self):
        self.check_pending["link"] = True
        found, _ = self.run_search(["google"], ["pdf"])
        self.assertEqual(len(self.questions), 1)
        self.assertIn("Google is showing a check in the browser window", self.questions[0])
        self.assertEqual(len(found), 3)

    def test_a_check_that_stays_ends_the_search_with_that_engine(self):
        self.check_pending["google"] = True
        self.checks_clear = False
        found, requests = self.run_search(["google", "bing"], ["pdf", "docx"])
        self.assertEqual(len(self.questions), 5)
        self.assertIn("[!] Google still shows a check. It is left out for the rest of this run.", self.messages)
        self.assertNotIn("Searching Google for 'site:localhost filetype:docx' in the browser", self.log())
        # Bing was still searched
        self.assertEqual(sorted(found), [self.target + "/files/b1.pdf", self.target + "/files/b2.pdf"])

    def test_a_check_on_result_links_that_stays_ends_the_search_with_that_engine(self):
        self.check_pending["link"] = True
        self.checks_clear = False
        found, _ = self.run_search(["google"], ["pdf", "docx"])
        self.assertEqual(len(self.questions), 3)
        self.assertEqual(self.messages.count("[!] Google still shows a check. It is left out for the rest of this run."), 1)
        self.assertNotIn("filetype:docx", self.log())
        # What the page showed without a lookup is kept
        self.assertEqual(sorted(found), [self.target + "/files/direct.pdf", self.target + "/files/wrapped.pdf"])

    def test_skipping_an_engine(self):
        self.check_pending["google"] = True
        self.answer = lambda question: "s"
        found, requests = self.run_search(["google", "bing"], ["pdf", "docx"])
        # Google was left alone after the first search; Bing was still asked
        self.assertEqual(sum(1 for request in requests if request.path == "/search"), 1)
        self.assertEqual(sorted(found), [self.target + "/files/b1.pdf", self.target + "/files/b2.pdf"])
        self.assertIn("[*] Google is skipped for the rest of this run.", self.messages)

    def test_pages_are_saved_when_asked_for(self):
        folder = os.path.join(self.profile, "saved pages")
        self.check_pending["google"] = True
        self.run_search(["google", "bing"], ["pdf"], save_pages=folder)
        names = sorted(os.listdir(folder))
        self.assertEqual(
            [name.split("-", 1)[1] for name in names],
            ["google-check.html", "google-pdf-page-1.html", "google-pdf-page-2.html", "bing-site.html", "bing-pdf-page-1.html", "bing-pdf-page-2.html", "bing-pdf-page-3.html"],
        )
        with open(os.path.join(folder, names[1]), encoding="utf-8") as saved:
            text = saved.read()
        self.assertTrue(text.startswith("<!-- http://127.0.0.1:"))
        self.assertIn("a result that hides its address", text)

    # -- Bing and DuckDuckGo --------------------------------------------------------------------------------

    def test_bing_pages(self):
        found, requests = self.run_search(["bing"], ["pdf"])
        self.assertEqual(found, [self.target + "/files/b1.pdf", self.target + "/files/b2.pdf"])
        asked = [request.query for request in requests if request.path == "/bing"]
        # First the site alone, then the file type page by page
        self.assertEqual(asked[0], {"q": ["site:localhost"]})
        self.assertEqual([query["first"][0] for query in asked[1:]], ["1", "4", "5"])

    def test_bing_is_left_out_when_it_does_not_keep_to_the_site(self):
        self.bing_site_answer = "others"
        found, requests = self.run_search(["bing", "duckduckgo"], ["pdf", "docx"])
        self.assertEqual(sum(1 for request in requests if request.path == "/bing"), 1)
        self.assertIn("with pages of other sites only", self.log())
        self.assertNotIn("filetype:pdf' in the browser\n[*] Now Analyzing page 1 of Bing", self.log())
        self.assertEqual(self.questions, [])
        # The other search engine was still asked
        self.assertIn(self.target + "/files/d1.pdf", found)

    def test_bing_is_left_out_when_it_has_nothing_on_the_site(self):
        self.bing_site_answer = "nothing"
        found, requests = self.run_search(["bing"], ["pdf"])
        self.assertEqual(found, [])
        self.assertEqual(sum(1 for request in requests if request.path == "/bing"), 1)
        self.assertEqual(len(self.questions), 1)
        self.assertIn("[*] Bing has no pages of localhost. It is left out for the rest of this run.", self.messages)

    def test_duckduckgo_next_button_and_check(self):
        self.check_pending["duckduckgo"] = True
        found, requests = self.run_search(["duckduckgo"], ["pdf"])
        target = self.target
        self.assertEqual(found, [target + "/files/d1.pdf", target + "/files/d-wrapped.pdf", target + "/files/d2.pdf"])
        self.assertEqual(len(self.questions), 1)
        posts = [request for request in requests if request.path == "/html/" and request.method == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].form.get("s"), "30")

    def test_duckduckgo_next_is_pressed_whatever_else_the_page_holds(self):
        # Another form with a query and a larger offset that leads elsewhere, and a control named
        # "submit" in the real form (which hides the form's own submit function from scripts)
        self.duckduckgo_extras = (
            '<form action="http://other.example/elsewhere" method="get"><input type="hidden" name="q" value="x">'
            '<input type="hidden" name="s" value="999999"></form>'
            "<script>document.querySelectorAll('form[action=\"/html/\"]').forEach(function (form) {"
            "  var extra = document.createElement('input'); extra.type = 'hidden'; extra.name = 'submit'; form.appendChild(extra);"
            "});</script>"
        )
        found, requests = self.run_search(["duckduckgo"], ["pdf"])
        self.assertIn(self.target + "/files/d2.pdf", found)
        self.assertEqual([request.path for request in requests if request.path not in ("/html/", "/favicon.ico")], [])

    def test_a_duckduckgo_check_on_a_later_page_that_stays_is_reported(self):
        self.check_pending["duckduckgo"] = "later pages"
        self.checks_clear = False
        found, _ = self.run_search(["duckduckgo"], ["pdf", "docx"])
        self.assertEqual(found, [self.target + "/files/d1.pdf", self.target + "/files/d-wrapped.pdf"])
        self.assertEqual(len(self.questions), 5)
        self.assertIn("[!] DuckDuckGo still shows a check. It is left out for the rest of this run.", self.messages)
        self.assertNotIn("filetype:docx", self.log())

    # -- when things go wrong ---------------------------------------------------------------------------------

    def test_closing_the_browser_ends_the_search_and_keeps_what_was_found(self):
        self.check_pending["google"] = True

        def close_the_browser(question):
            self.questions.append(question)
            try:
                self.sessions[-1].driver.execute_cdp_cmd("Browser.close", {})
            except Exception:
                pass  # the browser may be gone before it has answered
            time.sleep(1)
            return ""

        self.answer = close_the_browser
        started = time.time()
        found, _ = self.run_search(["bing", "google", "duckduckgo"], ["pdf", "docx"])
        self.assertEqual(sorted(found), [self.target + "/files/b1.pdf", self.target + "/files/b2.pdf"])
        self.assertEqual(len(self.questions), 1)
        self.assertEqual(sum(1 for message in self.messages if "The browser search stops here" in message), 1)
        self.assertNotIn("DuckDuckGo", self.log())
        self.assertLess(time.time() - started, 60)

    def test_closing_the_browser_during_a_lookup_ends_the_search_and_keeps_what_was_found(self):
        self.google_links = ["direct", "dead-1", "T1"]

        def close_the_browser(question):
            self.questions.append(question)
            try:
                self.sessions[-1].driver.execute_cdp_cmd("Browser.close", {})
            except Exception:
                pass
            time.sleep(1)
            return ""

        self.answer = close_the_browser
        found, _ = self.run_search(["bing", "google"], ["pdf", "docx"])
        target = self.target
        self.assertEqual(sorted(found), [target + "/files/b1.pdf", target + "/files/b2.pdf", target + "/files/direct.pdf"])
        self.assertEqual(sum(1 for message in self.messages if "The browser search stops here" in message), 1)
        self.assertNotIn("stopped early", self.log())

    def test_ctrl_c_is_passed_on_after_the_links_found_so_far_were_handed_over(self):
        self.check_pending["google"] = True

        def interrupt(question):
            raise KeyboardInterrupt

        self.answer = interrupt
        handed_over = []
        with self.assertRaises(KeyboardInterrupt):
            self.run_search(["bing", "google"], ["pdf"], on_link=handed_over.append)
        self.assertEqual(handed_over, [self.target + "/files/b1.pdf", self.target + "/files/b2.pdf"])
        # The browser was closed on the way out
        with self.assertRaises(Exception):
            self.sessions[-1].driver.current_url

    def test_ctrl_c_during_a_lookup_is_passed_on_too(self):
        self.google_links = ["direct", "dead-1"]

        def interrupt(question):
            raise KeyboardInterrupt

        self.answer = interrupt
        handed_over = []
        with self.assertRaises(KeyboardInterrupt):
            self.run_search(["google"], ["pdf"], on_link=handed_over.append)
        self.assertEqual(handed_over, [self.target + "/files/direct.pdf"])

    def test_an_error_that_was_not_foreseen_ends_the_browser_search_only(self):
        def broken(session, domain, file_type, max_pages, delay):
            raise RuntimeError("something new")

        with patched(browser, ENGINES=dict(browser.ENGINES, google=broken)):
            found, _ = self.run_search(["bing", "google"], ["pdf"])
        self.assertEqual(found, [self.target + "/files/b1.pdf", self.target + "/files/b2.pdf"])
        self.assertIn("The browser search stopped early (something new)", self.log())

    def test_a_profile_that_is_in_use_is_explained(self):
        with LocalServer(self.respond) as server:
            with self.started(server):
                first = browser.Session(log=self.messages.append, ask=self.answer)
                try:
                    with self.assertRaises(browser.BrowserUnavailable) as raised:
                        browser.Session(log=self.messages.append, ask=self.answer).close()
                    self.assertIn("close it first", str(raised.exception))
                    self.assertIn(self.profile, str(raised.exception))
                finally:
                    first.close()

    def test_the_browser_says_it_is_automated(self):
        with LocalServer(lambda request: (200, "<html><body>hello</body></html>")) as server:
            with patched(browser, EXTRA_ARGUMENTS=ARGUMENTS, PROFILE_FOLDER=self.profile):
                session = browser.Session(log=self.messages.append, ask=self.answer)
                try:
                    session._go(server.url + "/")
                    self.assertTrue(session.driver.execute_script("return navigator.webdriver"))
                    self.assertEqual(server.requests[0].headers["User-Agent"], session.driver.execute_script("return navigator.userAgent"))
                finally:
                    session.close()


if __name__ == "__main__":
    unittest.main()
