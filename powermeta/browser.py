"""Searching in a real, visible browser window.

Google no longer answers plain web requests, and Bing and DuckDuckGo turn them away on many networks.
This module opens a normal browser window through Selenium, runs the searches there, and reads the
result links from the pages. When a search engine shows a check or a consent box, the person at the
keyboard deals with it in the window and the run carries on.

Nothing here hides that the browser is automated: Selenium's own announcement is left in place, the
browser sends its real User-Agent, and no check is ever answered by the program.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse

from . import links

# Kept in variables so the tests can point them at a local server
GOOGLE_URL = "https://www.google.com/search"
BING_URL = "https://www.bing.com/search"
DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"

# Extra command-line arguments for the browser, and where it keeps its profile (the tests run it without a
# window and with a throw-away profile)
EXTRA_ARGUMENTS = []
PROFILE_FOLDER = None

NAMES = {"google": "Google", "bing": "Bing", "duckduckgo": "DuckDuckGo"}


class BrowserUnavailable(Exception):
    pass


def _host(url):
    return urllib.parse.urlsplit(url).netloc.lower()


def page_state(engine, url, source):
    """What a search engine is showing: "results", "empty" (no results), or "check" (a challenge or a
    consent box that a person has to deal with)."""
    source = source or ""
    if engine == "google":
        if "/sorry/" in url or re.match(r"https?://consent\.", url):
            return "check"
        # The check page, and the consent box that covers the results in some countries
        if re.search(r'id="captcha-form"|unusual traffic from your computer|id="W0wltc"|id="L2AGLb"', source):
            return "check"
        return "results"
    if engine == "duckduckgo":
        if links.is_duckduckgo_bot_check(source):
            return "check"
        return "results" if links.count_duckduckgo_results(source) else "empty"
    if engine == "bing":
        if links.count_bing_results(source):
            return "results"
        if "/turing/captcha" in url or re.search(r'id="b_captcha"|class="captcha|captcha_header', source, re.I):
            return "check"
        return "empty"
    return "results"


class Session:
    """One browser window, kept open for the whole run."""

    def __init__(self, browser_name="chrome", log=print, ask=input, profile_folder=None):
        self.log = log
        self.ask = ask
        self.skipped = set()
        self._asked_about_empty_page = set()
        try:
            from selenium import webdriver
            from selenium.common.exceptions import WebDriverException
        except ImportError:
            raise BrowserUnavailable("the browser mode needs Selenium. Install it with: pip install selenium")
        self._errors = WebDriverException

        profile_folder = profile_folder or PROFILE_FOLDER
        if profile_folder is None:
            # A folder of its own, so that an answered consent box or check is remembered for the next run
            profile_folder = os.path.join(os.path.expanduser("~"), ".powermeta", "browser-" + browser_name)
        os.makedirs(profile_folder, exist_ok=True)

        if browser_name == "edge":
            options = webdriver.EdgeOptions()
            logging_capability = "ms:loggingPrefs"
        else:
            options = webdriver.ChromeOptions()
            logging_capability = "goog:loggingPrefs"
        options.add_argument("--user-data-dir=" + profile_folder)
        for argument in EXTRA_ARGUMENTS:
            options.add_argument(argument)
        # The network log is how the address behind one of Google's redirect links is read
        options.set_capability(logging_capability, {"performance": "ALL"})
        # Result links point at documents: they must not open in the built-in viewer or be saved
        options.add_experimental_option("prefs", {"plugins.always_open_pdf_externally": True})

        try:
            self.driver = webdriver.Edge(options=options) if browser_name == "edge" else webdriver.Chrome(options=options)
        except WebDriverException as error:
            raise BrowserUnavailable("the browser could not be started: %s" % str(error).strip().split("\n")[0])
        self.driver.set_page_load_timeout(60)
        try:
            self.driver.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "deny"})
        except WebDriverException:
            pass

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    # -- reading the window ----------------------------------------------------------------------

    def _go(self, url):
        try:
            self.driver.get(url)
        except self._errors as error:
            self.log("[!] The browser could not open %s: %s" % (url, str(error).strip().split("\n")[0]))
            return False
        self._settle()
        return True

    def _settle(self, seconds=20):
        """Wait until the page in the window has finished loading."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    break
            except self._errors:
                pass
            time.sleep(0.25)
        time.sleep(0.5)

    def url(self):
        try:
            return self.driver.current_url
        except self._errors:
            return ""

    def source(self):
        try:
            return self.driver.page_source
        except self._errors:
            return ""

    def hrefs(self):
        """The address of every link on the page, as the browser resolved it."""
        try:
            found = self.driver.execute_script(
                "return Array.prototype.map.call(document.querySelectorAll('a[href]'), function (a) { return a.href; });"
            )
        except self._errors:
            return []
        return [href for href in (found or []) if isinstance(href, str)]

    # -- the person at the keyboard ---------------------------------------------------------------

    def show(self, engine, url):
        """Open a results page. If the search engine shows a check or a consent box instead, wait for the
        person to deal with it in the window. Returns False if the engine is to be left alone."""
        name = NAMES[engine]
        if not self._go(url):
            return False

        for _ in range(5):
            if page_state(engine, self.url(), self.source()) != "check":
                return True
            question = (
                "[?] %s is showing a check or a consent box in the browser window. Deal with it there, then "
                "press Enter here. (Type s and Enter to skip %s.) " % (name, name)
            )
            if not self._wait_for_person(engine, question):
                return False
            if not self._back_to(engine, url):
                return False
        return page_state(engine, self.url(), self.source()) != "check"

    def second_look(self, engine, url):
        """Called when nothing could be read from the first page of a search. A check this program does
        not recognise would look just like an empty page, so the person gets one chance per search engine
        to look at the window. Returns True if the page should be read again."""
        if engine in self._asked_about_empty_page or engine in self.skipped:
            return False
        self._asked_about_empty_page.add(engine)
        name = NAMES[engine]
        question = (
            "[?] Nothing could be read from %s's page. If the browser window shows a check or a consent box, "
            "deal with it there and then press Enter here. If the page simply has no results, just press Enter. "
            "(Type s and Enter to skip %s.) " % (name, name)
        )
        if not self._wait_for_person(engine, question):
            return False
        return self._back_to(engine, url)

    def _back_to(self, engine, url):
        """After the person has dealt with a check the browser normally lands back on the results.
        If it is somewhere else, or the window still shows the check, open the search again."""
        self._settle()
        if _host(self.url()) != _host(url) or page_state(engine, self.url(), self.source()) == "check":
            return self._go(url)
        return True

    def _wait_for_person(self, engine, question):
        try:
            answer = self.ask(question)
        except EOFError:
            answer = "s"
        if str(answer).strip().lower().startswith("s"):
            self.log("[*] %s is skipped for the rest of this run." % NAMES[engine])
            self.skipped.add(engine)
            return False
        return True

    # -- Google's hidden addresses ------------------------------------------------------------------

    def resolve(self, link, wait=8):
        """Find the address behind a redirect link by opening it in a second tab.

        The browser's network log shows where the redirect leads, also when the destination is a file
        the browser would otherwise download. Returns the address, or None if there was no redirect
        (for example because the search engine put a check in the way).
        """
        driver = self.driver
        results_tab = driver.current_window_handle
        destination = None
        try:
            driver.switch_to.new_window("tab")
            driver.get_log("performance")  # discard what was logged before
            try:
                driver.get(link)
            except self._errors:
                pass
            deadline = time.time() + wait
            hops = {}
            while True:
                time.sleep(0.4)
                for entry in driver.get_log("performance"):
                    try:
                        message = json.loads(entry["message"])["message"]
                    except (KeyError, ValueError):
                        continue
                    if message.get("method") != "Network.requestWillBeSent":
                        continue
                    parameters = message.get("params", {})
                    redirected_from = parameters.get("redirectResponse", {}).get("url")
                    if redirected_from:
                        hops[redirected_from] = parameters.get("request", {}).get("url")
                # Follow the chain of redirects from the link to where it ends
                current, seen = link, set()
                while current in hops and current not in seen:
                    seen.add(current)
                    current = hops[current]
                if current != link and current:
                    destination = current
                    break
                # No redirect yet: the tab may have gone on by itself (a page that forwards with a script)
                now = self.url()
                if now and now not in ("about:blank", link) and _host(now) != _host(link):
                    destination = now
                    break
                if time.time() >= deadline:
                    break
        except self._errors:
            destination = None
        finally:
            try:
                if driver.current_window_handle != results_tab:
                    driver.close()
            except self._errors:
                pass
            try:
                driver.switch_to.window(results_tab)
            except self._errors:
                pass
        return destination


def _keep(candidates, domain, file_type, found):
    """Add the wanted files among the candidates to found. Returns (new files, links on the domain)."""
    new, on_domain = 0, 0
    for link in candidates:
        if not link:
            continue
        if links.is_on_domain(link, domain):
            on_domain += 1
        if links.is_wanted_file(link, domain, file_type):
            link = links.plain_ascii(link)
            if link not in found:
                found.append(link)
                new += 1
    return new, on_domain


def search_google(session, domain, file_type, max_pages=5, delay=2):
    """Search Google for "site:domain filetype:type" in the browser window."""
    log = session.log
    found = []
    query = "site:%s filetype:%s" % (domain, file_type)
    own_hosts = (_host(GOOGLE_URL),)
    unresolved = 0

    log("[*] Searching Google for '%s' in the browser" % query)
    for page_number in range(1, max_pages + 1):
        if page_number > 1 and delay > 0:
            time.sleep(delay)
        log("[*] Now Analyzing page %d of Google search results" % page_number)
        url = GOOGLE_URL + "?" + urllib.parse.urlencode({"q": query, "start": (page_number - 1) * 10, "filter": "0"})
        if not session.show("google", url):
            break

        def read():
            addresses, hidden = [], []
            for href in session.hrefs():
                kind, value = links.classify_google_link(href, own_hosts)
                if kind == "address":
                    addresses.append(value)
                elif kind == "redirect" and value not in hidden:
                    hidden.append(value)
            return addresses, hidden

        addresses, hidden = read()
        if page_number == 1 and not addresses and not hidden and session.second_look("google", url):
            addresses, hidden = read()

        # Google's result links are redirects that do not show where they lead; each one is opened in a
        # second tab to find out
        if hidden:
            log("[*] Looking up the addresses behind %d result links" % len(hidden))
        for link in hidden:
            destination = session.resolve(link)
            if destination:
                addresses.append(destination)
            else:
                unresolved += 1
            if delay > 0:
                time.sleep(min(delay, 1))

        new, _ = _keep(addresses, domain, file_type, found)
        # No result links at all, or nothing new: this was the last page
        if (not addresses and not hidden) or new == 0:
            break

    if unresolved:
        log("[!] The address behind %d Google result links could not be read." % unresolved)
    log("[*] Google (browser): %d %s files found" % (len(found), file_type))
    return found


def search_bing(session, domain, file_type, max_pages=5, delay=2):
    """Search Bing for "site:domain filetype:type" in the browser window."""
    log = session.log
    found = []
    query = "site:%s filetype:%s" % (domain, file_type)
    first, results_seen, on_domain_seen = 1, 0, 0

    log("[*] Searching Bing for '%s' in the browser" % query)
    for page_number in range(1, max_pages + 1):
        if page_number > 1 and delay > 0:
            time.sleep(delay)
        log("[*] Now Analyzing page %d of Bing search results" % page_number)
        url = BING_URL + "?" + urllib.parse.urlencode({"q": query, "first": first})
        if not session.show("bing", url):
            break
        results = links.count_bing_results(session.source())
        if page_number == 1 and results == 0 and session.second_look("bing", url):
            results = links.count_bing_results(session.source())
        new, on_domain = _keep([links.unwrap_bing(href) for href in session.hrefs()], domain, file_type, found)
        results_seen += results
        on_domain_seen += on_domain
        if results == 0 or new == 0:
            break
        first += results

    if results_seen > 0 and on_domain_seen == 0:
        log("[!] Bing returned results, but none of them are on %s." % domain)
    log("[*] Bing (browser): %d %s files found" % (len(found), file_type))
    return found


def search_duckduckgo(session, domain, file_type, max_pages=5, delay=2):
    """Search DuckDuckGo's script-free page for "site:domain filetype:type" in the browser window."""
    log = session.log
    found = []
    query = "site:%s filetype:%s" % (domain, file_type)
    offset = 0

    log("[*] Searching DuckDuckGo for '%s' in the browser" % query)
    url = DUCKDUCKGO_URL + "?" + urllib.parse.urlencode({"q": query})
    if not session.show("duckduckgo", url):
        return found
    if links.count_duckduckgo_results(session.source()) == 0:
        session.second_look("duckduckgo", url)
    for page_number in range(1, max_pages + 1):
        log("[*] Now Analyzing page %d of DuckDuckGo search results" % page_number)
        new, _ = _keep([links.unwrap_duckduckgo(href) for href in session.hrefs()], domain, file_type, found)
        if new == 0 or page_number == max_pages:
            break

        # The page's own "Next" button leads to the following page
        next_form = links.duckduckgo_next_form(session.source(), after=offset)
        if next_form is None:
            break
        if delay > 0:
            time.sleep(delay)
        try:
            pressed = session.driver.execute_script(
                "var forms = document.querySelectorAll('form');"
                "for (var i = 0; i < forms.length; i++) {"
                "  var s = forms[i].querySelector('input[name=\"s\"]');"
                "  if (s && s.value === arguments[0]) { forms[i].submit(); return true; }"
                "}"
                "return false;",
                next_form["s"],
            )
        except session._errors:
            pressed = False
        if not pressed:
            break
        offset = int(next_form["s"])
        session._settle()
        if page_state("duckduckgo", session.url(), session.source()) == "check":
            if not session._wait_for_person(
                "duckduckgo",
                "[?] DuckDuckGo is showing a check in the browser window. Deal with it there, then press Enter "
                "here. (Type s and Enter to skip DuckDuckGo.) ",
            ):
                break
            session._settle()

    log("[*] DuckDuckGo (browser): %d %s files found" % (len(found), file_type))
    return found


ENGINES = {"google": search_google, "bing": search_bing, "duckduckgo": search_duckduckgo}


def search(engines, domain, file_types, max_pages=5, delay=2, browser_name="chrome", log=print, ask=input):
    """Run the searches of every named engine and file type in one browser window. Returns the links found."""
    try:
        session = Session(browser_name, log, ask)
    except BrowserUnavailable as error:
        log("[!] The browser search was not done: %s" % error)
        return []

    log(
        "[*] A browser window has been opened. If a search engine shows a check or a consent box, "
        "you will be asked to deal with it there."
    )
    found = []
    try:
        for engine in engines:
            first = True
            for file_type in file_types:
                if engine in session.skipped:
                    break
                if not first and delay > 0:
                    time.sleep(delay)
                first = False
                for link in ENGINES[engine](session, domain, file_type, max_pages, delay):
                    if link not in found:
                        found.append(link)
    finally:
        session.close()
    return found
