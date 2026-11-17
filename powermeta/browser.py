"""Searching in a real, visible browser window.

Google no longer answers plain web requests, and Bing and DuckDuckGo turn them away on many networks.
This module opens a normal browser window through Selenium, runs the searches there, and reads the
result links from the pages. When a search engine shows a check or a consent box, the person at the
keyboard deals with it in the window and the run carries on.

Nothing here hides that the browser is automated: Selenium's own announcement is left in place, the
browser sends its real User-Agent, and no check is ever answered by the program.

Google's result links do not show where they lead. To find out, each one is opened in a second tab,
which is closed again as soon as the address is known. The browser asks the site for that address when
it does this, exactly as it would if a person clicked the result; nothing is saved.
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

# Seconds a page may take to load, and seconds a result link gets to show where it leads
PAGE_LOAD_WAIT = 60
LOOKUP_WAIT = 8

NAMES = {"google": "Google", "bing": "Bing", "duckduckgo": "DuckDuckGo"}

# The form of Google's check page, and the buttons of the consent box that covers the results in some
# countries. Only real elements count: the same words in a result's title or snippet do not.
_GOOGLE_CHECK = re.compile(r'<[a-z][^<>]*\sid="(?:captcha-form|W0wltc|L2AGLb)"', re.I)
_SCRIPTS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_LOST = re.compile(
    r"no such window|window already closed|invalid session id|not connected to DevTools|not reachable|"
    r"session deleted|web view not found|disconnected|tab crashed",
    re.I,
)


class BrowserUnavailable(Exception):
    pass


def _host(url):
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _first_line(error):
    """The first line of an error text, without Selenium's trimmings."""
    text = str(error).strip().split("\n")[0].split("; For documentation")[0].strip()
    text = re.sub(r"^Message:\s*", "", text)
    return text or error.__class__.__name__


def page_state(engine, url, source):
    """What a search engine is showing: "results", "empty" (no results), or "check" (a challenge or a
    consent box that a person has to deal with)."""
    url = url or ""
    source = source or ""
    if engine == "google":
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return "results"
        if "/sorry/" in parts.path or (parts.hostname or "").startswith("consent."):
            return "check"
        if _GOOGLE_CHECK.search(_SCRIPTS.sub("", source)):
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

    def __init__(self, browser_name="chrome", log=print, ask=input, profile_folder=None, network_log=True, on_link=None):
        self.log = log
        self.ask = ask
        self.on_link = on_link
        self.found = []  # every link found in this window, in the order found
        self.skipped = set()  # search engines that are left alone for the rest of the run
        self.gone = False  # the window was closed, or the browser can no longer be reached
        self.lookups_off = False  # result links that hide their address are no longer followed
        self._asked_about_empty_page = set()
        self._asked_about_link = False
        self._unresolved_in_a_row = 0
        try:
            import selenium
            from selenium import webdriver
            from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
        except ImportError:
            raise BrowserUnavailable("the browser mode needs Selenium. Install it with: pip install selenium")
        version = str(getattr(selenium, "__version__", ""))
        if not re.match(r"([4-9]|[1-9][0-9]+)\.", version):
            raise BrowserUnavailable(
                "the browser mode needs Selenium 4 or newer (this is Selenium %s). Update it with: "
                "pip install --upgrade selenium" % (version or "of an unknown version")
            )
        self._errors = WebDriverException
        self._stale = StaleElementReferenceException

        profile_folder = profile_folder or PROFILE_FOLDER
        if profile_folder is None:
            # A folder of its own, so that an answered consent box or check is remembered for the next run
            profile_folder = os.path.join(os.path.expanduser("~"), ".powermeta", "browser-" + browser_name)
        try:
            os.makedirs(profile_folder, exist_ok=True)
        except OSError as error:
            raise BrowserUnavailable("the profile folder %s cannot be used: %s" % (profile_folder, error))

        if browser_name == "edge":
            options = webdriver.EdgeOptions()
            logging_capability = "ms:loggingPrefs"
        else:
            options = webdriver.ChromeOptions()
            logging_capability = "goog:loggingPrefs"
        options.add_argument("--user-data-dir=" + profile_folder)
        for argument in EXTRA_ARGUMENTS:
            options.add_argument(argument)
        if network_log:
            # The network log is how the address behind one of Google's redirect links is read
            options.set_capability(logging_capability, {"performance": "ALL"})
        # Result links point at documents: they must not open in the built-in viewer or be saved
        options.add_experimental_option("prefs", {"plugins.always_open_pdf_externally": True})

        try:
            self.driver = webdriver.Edge(options=options) if browser_name == "edge" else webdriver.Chrome(options=options)
        except Exception as error:
            hint = (
                " If another PowerMeta browser window is still open, close it first: both would use the "
                "profile folder %s." % profile_folder
            )
            if browser_name == "edge":
                hint += (
                    " An older Selenium (the newest that Python 3.8 can install is one) cannot fetch the Edge "
                    "driver by itself any more: put a matching msedgedriver on the PATH, or use Chrome."
                )
            raise BrowserUnavailable("the browser could not be started: %s.%s" % (_first_line(error).rstrip("."), hint))
        try:
            self.driver.set_page_load_timeout(PAGE_LOAD_WAIT)
            self.driver.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "deny"})
        except WebDriverException:
            pass

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def keep(self, link):
        """Remember a link that was found, and hand it to whoever asked to be told at once."""
        if link not in self.found:
            self.found.append(link)
            if self.on_link is not None:
                self.on_link(link)

    def _lose(self):
        if not self.gone:
            self.log(
                "[!] The browser window has been closed, or the browser can no longer be reached. "
                "The browser search stops here."
            )
        self.gone = True

    def _failed(self, error):
        """Look at a browser command that failed. Returns True if the window is gone."""
        if _LOST.search(str(error)):
            self._lose()
        return self.gone

    # -- reading the window ----------------------------------------------------------------------

    def _go(self, url):
        if self.gone:
            return False
        try:
            self.driver.get(url)
        except self._errors as error:
            if not self._failed(error):
                self.log("[!] The browser could not open %s: %s" % (url, _first_line(error)))
            return False
        self._settle()
        return not self.gone

    def _settle(self, seconds=20):
        """Wait until the page in the window has finished loading."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    break
            except self._errors as error:
                if self._failed(error):
                    return
            time.sleep(0.25)
        time.sleep(0.5)

    def url(self):
        try:
            return self.driver.current_url
        except self._errors as error:
            self._failed(error)
            return ""

    def source(self):
        try:
            return self.driver.page_source
        except self._errors as error:
            self._failed(error)
            return ""

    def hrefs(self):
        """The address of every link on the page, as the browser resolved it."""
        try:
            found = self.driver.execute_script(
                "return Array.prototype.map.call(document.querySelectorAll('a[href]'), function (a) { return a.href; });"
            )
        except self._errors as error:
            self._failed(error)
            return []
        return [href for href in (found or []) if isinstance(href, str)]

    def press_form(self, offset):
        """Press the form on the page whose offset field ("s") has this value, and wait for the page it
        leads to. Only a form that leads back to the same site is pressed. Returns True if a new page
        has been loaded."""
        try:
            old_page = self.driver.find_element("tag name", "html")
            pressed = self.driver.execute_script(
                "var forms = document.querySelectorAll('form');"
                "for (var i = 0; i < forms.length; i++) {"
                "  var s = forms[i].querySelector('input[name=\"s\"]');"
                "  var action = new URL(forms[i].getAttribute('action') || '', location.href);"
                "  if (s && s.value === arguments[0] && action.host === location.host) {"
                "    HTMLFormElement.prototype.submit.call(forms[i]); return true;"
                "  }"
                "}"
                "return false;",
                offset,
            )
        except self._errors as error:
            self._failed(error)
            return False
        if not pressed:
            return False
        # The old page stays in the window until the answer arrives: wait until it has been replaced
        deadline = time.time() + PAGE_LOAD_WAIT
        while time.time() < deadline:
            try:
                old_page.is_enabled()
            except self._stale:
                self._settle()
                return not self.gone
            except self._errors as error:
                self._failed(error)
                return False
            time.sleep(0.2)
        return False

    # -- the person at the keyboard ---------------------------------------------------------------

    def show(self, engine, url):
        """Open a results page. If the search engine shows a check or a consent box instead, wait for the
        person to deal with it in the window. Returns False if the engine is to be left alone."""
        if not self._go(url):
            return False
        return self.clear_check(engine, url)

    def clear_check(self, engine, url=None):
        """While the window shows a check or a consent box, ask the person to deal with it. Returns False
        if the engine is to be left alone: the person said so, the check did not go away, or the window
        is gone. url is the search to open again if the window ends up somewhere else."""
        name = NAMES[engine]
        question = (
            "[?] %s is showing a check or a consent box in the browser window. Deal with it there, then "
            "press Enter here. (Type s and Enter to skip %s.) " % (name, name)
        )
        for _ in range(5):
            state = page_state(engine, self.url(), self.source())
            if self.gone:
                return False
            if state != "check":
                return True
            if not self._wait_for_person(engine, question):
                return False
            if not self._back_to(engine, url):
                return False
        if page_state(engine, self.url(), self.source()) != "check":
            return not self.gone
        if not self.gone:
            self.log("[!] %s still shows a check. It is left out for the rest of this run." % name)
            self.skipped.add(engine)
        return False

    def second_look(self, engine, url):
        """Called when nothing could be read from the first page of a search. A check this program does
        not recognise would look just like an empty page, so the person gets one chance per search engine
        to look at the window. Returns True if the page should be read again."""
        if engine in self._asked_about_empty_page or engine in self.skipped or self.gone:
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
        if self.gone:
            return False
        if url is None:
            return True
        if _host(self.url()) != _host(url) or page_state(engine, self.url(), self.source()) == "check":
            return self._go(url)
        return not self.gone

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

    def resolve(self, link):
        """Find the address behind a redirect link by opening it in a second tab.

        The browser's network log shows where the link leads, also when that is a file the browser would
        otherwise download. Only the first address outside the search engine is wanted: where the site
        sends the browser from there is not what the search engine listed. If the search engine puts a
        check in the way, the person is asked to deal with it. Returns the address, or None.
        """
        if self.gone:
            return None
        driver = self.driver
        try:
            results_tab = driver.current_window_handle
            before = set(driver.window_handles)
        except self._errors as error:
            self._failed(error)
            return None

        start = urllib.parse.urldefrag(link)[0]  # the network log leaves the "#..." part out
        own = (_host(link),)

        def outside(address):
            """The address, if it is not one of the search engine's own."""
            kind, value = links.classify_google_link(address or "", own)
            return value if kind == "address" else None

        documents = []  # (frame, address) of every page the browser asked for, in order

        def read_log():
            for entry in driver.get_log("performance"):
                try:
                    message = json.loads(entry["message"])["message"]
                    if message["method"] != "Network.requestWillBeSent":
                        continue
                    parameters = message["params"]
                    if parameters.get("type") not in (None, "Document"):
                        continue
                    documents.append((parameters.get("frameId"), parameters["request"]["url"]))
                except (KeyError, TypeError, ValueError):
                    continue

        def destination_in_log():
            # The tab asks for the link first. The first address outside the search engine that the same
            # tab asks for after that is where the link leads: through a redirect, or because the page
            # sent the browser on by itself.
            frame, opened = None, False
            for document_frame, address in documents:
                if not opened:
                    if urllib.parse.urldefrag(address)[0] == start:
                        frame, opened = document_frame, True
                elif document_frame == frame and outside(address):
                    return outside(address)
            return None

        def open_link():
            try:
                driver.get(link)
            except self._errors:
                pass  # a file that is not shown, a page that takes too long: the log is what counts

        destination = None
        checks, dealt_with, opened_again = 0, False, False
        try:
            driver.switch_to.new_window("tab")
            driver.get_log("performance")  # discard what was logged before
            driver.set_page_load_timeout(LOOKUP_WAIT)
            deadline = time.time() + LOOKUP_WAIT
            open_link()
            while True:
                read_log()
                destination = destination_in_log()
                if destination:
                    break
                now = driver.current_url
                if outside(now):
                    destination = outside(now)
                    break
                if page_state("google", now, driver.page_source) == "check":
                    if checks >= 3:
                        self.log("[!] Google still shows a check. It is left out for the rest of this run.")
                        self.skipped.add("google")
                        break
                    checks += 1
                    question = (
                        "[?] Google is showing a check in the browser window, on the link of a result. Deal "
                        "with it there, then press Enter here. (Type s and Enter to skip Google.) "
                    )
                    if not self._wait_for_person("google", question):
                        break
                    deadline = time.time() + LOOKUP_WAIT
                    read_log()
                    if not destination_in_log() and page_state("google", driver.current_url, driver.page_source) == "check":
                        # The tab still shows the check: open the link again to see whether it is still asked for
                        open_link()
                    else:
                        dealt_with = True
                    continue
                if _host(now) == own[0]:
                    # A notice in between that names the address ("you are being sent to ...")
                    hrefs = driver.execute_script(
                        "return Array.prototype.map.call(document.querySelectorAll('a[href]'), function (a) { return a.href; });"
                    )
                    named = set(filter(None, (outside(href) for href in hrefs or [] if isinstance(href, str))))
                    if len(named) == 1:
                        destination = named.pop()
                        break
                if time.time() >= deadline:
                    if dealt_with and not opened_again:
                        # The check is out of the way, but the browser was not sent on: open the link again
                        opened_again, deadline = True, time.time() + LOOKUP_WAIT
                        open_link()
                        continue
                    if not self._asked_about_link:
                        # A check this program does not recognise looks just like a link that leads nowhere
                        self._asked_about_link = True
                        question = (
                            "[?] The address behind a Google result could not be read. If the browser window "
                            "shows a check, deal with it there and then press Enter here. Otherwise just "
                            "press Enter. (Type s and Enter to skip Google.) "
                        )
                        if not self._wait_for_person("google", question):
                            break
                        dealt_with, deadline = True, time.time() + LOOKUP_WAIT
                        continue
                    break
                time.sleep(0.4)
        except self._errors:
            # The page in the tab closed it, or put up a dialog: what the log held has been read by now
            destination = destination or destination_in_log()
        finally:
            # Close the tab, and any window the page in it opened. Nothing here may get in the way of an
            # error that is already on its way up.
            try:
                driver.switch_to.window(results_tab)
            except Exception:
                pass
            for attempt in range(4):
                try:
                    extra = [handle for handle in driver.window_handles if handle not in before]
                    if not extra:
                        break
                    for handle in extra:
                        if attempt % 2 == 0:
                            # Asked of the browser from the results tab: a page that keeps putting up
                            # dialogs cannot stop this
                            driver.execute_cdp_cmd("Target.closeTarget", {"targetId": handle.replace("CDwindow-", "")})
                        else:
                            driver.switch_to.window(handle)
                            driver.close()
                except Exception:
                    pass
                time.sleep(0.1)
            try:
                driver.switch_to.window(results_tab)
                driver.set_page_load_timeout(PAGE_LOAD_WAIT)
            except Exception:
                self._lose()

        if self.gone or "google" in self.skipped:
            return None
        if destination:
            self._unresolved_in_a_row = 0
        else:
            self._unresolved_in_a_row += 1
            if self._unresolved_in_a_row >= 3:
                self.log(
                    "[!] Three Google result links in a row did not show where they lead. Result links "
                    "that hide their address are not followed for the rest of this run."
                )
                self.lookups_off = True
        return destination


def _keep(session, candidates, domain, file_type, found):
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
                session.keep(link)
    return new, on_domain


def search_google(session, domain, file_type, max_pages=5, delay=2):
    """Search Google for "site:domain filetype:type" in the browser window."""
    log = session.log
    found = []
    query = "site:%s filetype:%s" % (domain, file_type)
    own_hosts = (_host(GOOGLE_URL),)
    unread = 0

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
        new, _ = _keep(session, addresses, domain, file_type, found)

        # Google's result links are redirects that do not show where they lead; each one is opened in a
        # second tab to find out
        if hidden and not session.lookups_off:
            log("[*] Looking up the addresses behind %d result links" % len(hidden))
        for position, link in enumerate(hidden):
            if session.lookups_off or session.gone or "google" in session.skipped:
                unread += len(hidden) - position
                break
            if position > 0 and delay > 0:
                time.sleep(delay)
            destination = session.resolve(link)
            if destination:
                new += _keep(session, [destination], domain, file_type, found)[0]
            else:
                unread += 1

        if session.gone or "google" in session.skipped:
            break
        # No result links at all, or nothing new: this was the last page
        if (not addresses and not hidden) or new == 0:
            break

    if unread:
        log("[!] The address behind %d Google result links was not read." % unread)
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
        new, on_domain = _keep(session, [links.unwrap_bing(href) for href in session.hrefs()], domain, file_type, found)
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
    if session.show("duckduckgo", url):
        if links.count_duckduckgo_results(session.source()) == 0:
            session.second_look("duckduckgo", url)
        for page_number in range(1, max_pages + 1):
            if session.gone or "duckduckgo" in session.skipped:
                break
            log("[*] Now Analyzing page %d of DuckDuckGo search results" % page_number)
            new, _ = _keep(session, [links.unwrap_duckduckgo(href) for href in session.hrefs()], domain, file_type, found)
            if new == 0 or page_number == max_pages:
                break

            # The page's own "Next" button leads to the following page
            next_form = links.duckduckgo_next_form(session.source(), after=offset)
            if next_form is None:
                break
            if delay > 0:
                time.sleep(delay)
            if not session.press_form(next_form["s"]):
                if not session.gone:
                    log("[!] The next page of DuckDuckGo's results could not be opened. The results so far are kept.")
                break
            offset = int(next_form["s"])
            # A check can also come up on a later page
            if not session.clear_check("duckduckgo"):
                break

    log("[*] DuckDuckGo (browser): %d %s files found" % (len(found), file_type))
    return found


ENGINES = {"google": search_google, "bing": search_bing, "duckduckgo": search_duckduckgo}


def search(engines, domain, file_types, max_pages=5, delay=2, browser_name="chrome", log=print, ask=input, on_link=None):
    """Run the searches of every named engine and file type in one browser window. Returns the links found.

    on_link, if given, is called with each link as soon as it is found, so that the caller has what was
    found when the search ends early. Ctrl+C is passed on to the caller, after the browser has been closed.
    """
    try:
        session = Session(browser_name, log, ask, network_log="google" in engines, on_link=on_link)
    except BrowserUnavailable as error:
        log("[!] The browser search was not done: %s" % error)
        return []

    log(
        "[*] A browser window has been opened. If a search engine shows a check or a consent box, "
        "you will be asked to deal with it there."
    )
    try:
        for engine in engines:
            first = True
            for file_type in file_types:
                if session.gone or engine in session.skipped:
                    break
                if not first and delay > 0:
                    time.sleep(delay)
                first = False
                ENGINES[engine](session, domain, file_type, max_pages, delay)
            if session.gone:
                break
    except KeyboardInterrupt:
        raise
    except Exception as error:
        # Selenium or the browser failed in a way that was not foreseen: keep what was found
        log("[!] The browser search stopped early (%s). What it had found is kept." % _first_line(error))
    finally:
        session.close()
    return session.found
