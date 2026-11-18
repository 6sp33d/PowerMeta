"""Searching in a real, visible browser window.

Google no longer answers plain web requests, and Bing and DuckDuckGo turn them away on many networks.
This module opens a normal browser window through Selenium, runs the searches there, and reads the
result links from the pages. When a search engine shows a check or a consent box, the person at the
keyboard deals with it in the window and the run carries on.

Nothing here hides that the browser is automated: Selenium's own announcement is left in place, the
browser sends its real User-Agent, and no check is ever answered by the program.

Google's result links do not show where they lead: the page holds a redirect link, the name of the site
and a shortened path, but not the address. To read it, the result is clicked in the results page, the way
a person clicks it, and the browser's network log shows where the click leads. A document is not opened
or saved by this (the browser is told not to download), so the window simply stays on the results.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse

from . import links, sources, web

# Where each search engine is asked
GOOGLE_URL = "https://www.google.com/search"
BING_URL = "https://www.bing.com/search"
DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"

# Seconds a page may take to load; seconds a result link gets to show where it leads; seconds a click
# gets to reach the search engine; and seconds a page of the search engine may stay in the window
# before the person is asked to look at it
PAGE_LOAD_WAIT = 60
LOOKUP_WAIT = 8
CLICK_WAIT = 3
SHOWN_WAIT = 2

NAMES = {"google": "Google", "bing": "Bing", "duckduckgo": "DuckDuckGo"}
LOOKUPS = ("click", "request", "off")

# The form of Google's check page, and the buttons of the consent box that covers the results in some
# countries. Only real elements count: the same words in a result's title or snippet do not.
_GOOGLE_CHECK = re.compile(r'<[a-z][^<>]*\sid="(?:captcha-form|W0wltc|L2AGLb)"', re.I)
_SEARCH_BOX = re.compile(r"""<(?:input|textarea)\b[^<>]*\sname=(?:"q"|'q'|q(?=[\s/>]))""", re.I)
_SCRIPTS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_LOST = re.compile(
    r"no such window|window already closed|invalid session id|not connected to DevTools|not reachable|"
    r"session deleted|web view not found|disconnected|tab crashed",
    re.I,
)

# The link of a result as it is shown in the page: the first one with a size, the result's title if
# there are several. It is moved into view so that it can be clicked.
# A result that would open in a new window is made to open in this one: only what happens in the results
# window can be read from the network log.
_FIND_LINK = (
    "var wanted = arguments[0], found = null, all = document.querySelectorAll('a[href]');"
    "for (var i = 0; i < all.length; i++) {"
    "  var a = all[i];"
    "  if (a.href !== wanted) continue;"
    "  var box = a.getBoundingClientRect();"
    "  if (box.width < 2 || box.height < 2) continue;"
    "  if (!found || (a.querySelector('h3') && !found.querySelector('h3'))) found = a;"
    "}"
    "if (found) {"
    "  var elsewhere = found.target ? found.target !== '_self' : document.querySelector('base[target]') !== null;"
    "  if (elsewhere) found.setAttribute('target', '_self');"
    "  found.scrollIntoView({block: 'center'});"
    "}"
    "return found;"
)
_ALL_LINKS = "return Array.prototype.map.call(document.querySelectorAll('a[href]'), function (a) { return a.href; });"
# The links of a page that only tells where the browser is being sent: a page that asks for something
# (a form, a button) is not such a page
_NOTICE_LINKS = "if (document.querySelector('form, button, input[type=submit], input[type=button]')) return [];" + _ALL_LINKS
_FORWARDS = re.compile(r"<meta\b[^<>]*http-equiv=[\"']?refresh", re.I)

_NOT_OPENED = object()  # the browser never asked for the link


class BrowserUnavailable(Exception):
    pass


def _host(url):
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _page(url):
    """An address without its "#..." part, which is what tells one page from another."""
    return urllib.parse.urldefrag(url or "")[0]


def _first_line(error):
    """The first line of an error text, without Selenium's trimmings."""
    text = str(error).strip().split("\n")[0].split("; For documentation")[0].strip()
    text = re.sub(r"^Message:\s*", "", text)
    return text or error.__class__.__name__


def _profile_folder(browser_name):
    """The folder the browser keeps its profile in. It is a folder of its own, so that an answered consent
    box or check is remembered for the next run and the person's own browser profile is left alone."""
    return os.path.join(os.path.expanduser("~"), ".justosint", "browser-" + browser_name)


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


def has_search_box(source):
    """A results page of a search engine always holds the box the search was typed into. A page without
    one is something else: a check, a notice, a button that has to be pressed first."""
    return _SEARCH_BOX.search(_SCRIPTS.sub("", source or "")) is not None


class Session:
    """One browser window, kept open for the whole run."""

    def __init__(self, browser_name="chrome", log=print, ask=input, network_log=True, on_link=None,
                 lookup="click", user_agent=None):
        self.log = log
        self.ask = ask
        self.on_link = on_link
        self.lookup = lookup  # how the address of a result that hides it is read: see LOOKUPS
        self.user_agent = user_agent or web.PROGRAM_USER_AGENT  # for lookups with plain requests
        self.found = []  # every link found in this window, in the order found
        self.per_engine = {}  # {search engine: the links it gave}
        self.skipped = set()  # search engines that are left alone for the rest of the run
        self.gone = False  # the window was closed, or the browser can no longer be reached
        self.lookups_off = lookup == "off"  # results that hide their address are left out
        self.results_reloaded = False  # the results page had to be opened again after a lookup
        self.delay = 0  # seconds between two requests to the same search engine
        self._click_problem = ""
        self._said_not_clickable = False
        self._asked_about_empty_page = set()
        self._unresolved_in_a_row = 0
        self._requests_refused = 0
        self._resolved = {}
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

        profile_folder = _profile_folder(browser_name)
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
        if network_log:
            # The network log is how the address behind one of Google's redirect links is read
            options.set_capability(logging_capability, {"performance": "ALL"})
        # Result links point at documents: they must not open in the built-in viewer, and nothing is saved
        options.add_experimental_option("prefs", {"plugins.always_open_pdf_externally": True, "download_restrictions": 3})

        def start(service=None):
            driver_class = webdriver.Edge if browser_name == "edge" else webdriver.Chrome
            return driver_class(options=options) if service is None else driver_class(options=options, service=service)

        try:
            self.driver = self._start_apart_from_ctrl_c(start, browser_name) or start()
        except Exception as error:
            hint = (
                " If another justOSINT browser window is still open, close it first: both would use the "
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
        except WebDriverException:
            pass
        self._deny_downloads()

    @staticmethod
    def _start_apart_from_ctrl_c(start, browser_name):
        """Windows only: start the browser's driver in a process group of its own. Ctrl+C would otherwise
        end the driver at once, and the browser could no longer be asked to close. Returns the started
        browser, or None if it has to be started the ordinary way."""
        if os.name != "nt":
            return None
        try:
            if browser_name == "edge":
                from selenium.webdriver.edge.service import Service
            else:
                from selenium.webdriver.chrome.service import Service
            service = Service()
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            # The name of this setting differs between Selenium releases
            service.creation_flags = flags
            service.creationflags = flags
            return start(service)
        except Exception:
            return None

    def _deny_downloads(self):
        """Tell the browser not to save what a click leads to. This is given through a tab and ends with
        that tab, so it is said again whenever another tab takes over."""
        try:
            self.driver.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "deny"})
        except Exception:
            pass

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def keep(self, link, engine=None):
        """Remember a link that was found, and hand it to whoever asked to be told at once."""
        self.per_engine.setdefault(engine, set()).add(link)
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
            found = self.driver.execute_script(_ALL_LINKS)
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

    def clear_check(self, engine, url=None, in_the_way=None):
        """While the window shows a check or a consent box, ask the person to deal with it. Returns False
        if the engine is to be left alone: the person said so, the check did not go away, or the window
        is gone. url is the search to open again if the window ends up somewhere else. in_the_way, if
        given, is a function that says whether something still has to be dealt with (by default: whether
        the window shows a check this program recognises)."""
        name = NAMES[engine]
        if in_the_way is None:
            def in_the_way():
                return page_state(engine, self.url(), self.source()) == "check"

            question = (
                "[?] %s is showing a check or a consent box in the browser window. Deal with it there, then "
                "press Enter here. (Type s and Enter to skip %s.) " % (name, name)
            )
        else:
            question = (
                "[?] %s is showing something else than results in the browser window (a check, or a button "
                "that has to be pressed first). Deal with it there, then press Enter here. "
                "(Type s and Enter to skip %s.) " % (name, name)
            )
        for _ in range(5):
            blocked = in_the_way()
            if self.gone:
                return False
            if not blocked:
                return True
            if not self._wait_for_person(engine, question):
                return False
            if not self._back_to(engine, url, in_the_way):
                return False
        if not in_the_way():
            return not self.gone
        if not self.gone:
            what = "a check" if "check or a consent box" in question else "something else than results"
            self.log("[!] %s still shows %s. It is left out for the rest of this run." % (name, what))
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
            "[?] Nothing could be read from %s's page. If the browser window shows a check, deal with it there, "
            "then press Enter here. If the page simply has no results, just press Enter. (s and Enter skips %s.) "
            % (name, name)
        )
        # With nobody there to look, the page is taken for what it most likely is: one without results
        if not self._wait_for_person(engine, question, unattended=""):
            return False
        return self._back_to(engine, url)

    def _back_to(self, engine, url, in_the_way=None):
        """After the person has dealt with a check the browser normally lands back on the results.
        If it is somewhere else, or the window still shows the check, open the search again."""
        self._settle()
        if self.gone:
            return False
        if url is None:
            return True
        if in_the_way is None:
            blocked = page_state(engine, self.url(), self.source()) == "check"
        else:
            blocked = in_the_way()
        if _host(self.url()) != _host(url) or blocked:
            return self._go(url)
        return not self.gone

    def _answer(self, question, unattended="s"):
        """What the person typed, in lower case. If nobody can be asked (there is no terminal), the
        answer is the one given as unattended: by default "s", skip."""
        try:
            return str(self.ask(question)).strip().lower()
        except EOFError:
            return unattended

    def _skip(self, engine):
        self.log("[*] %s is skipped for the rest of this run." % NAMES[engine])
        self.skipped.add(engine)

    def _wait_for_person(self, engine, question, unattended="s"):
        if self._answer(question, unattended).startswith("s"):
            self._skip(engine)
            return False
        return True

    # -- Google's hidden addresses ------------------------------------------------------------------

    def resolve(self, link, results_url=None, domain=None):
        """Find the address behind a result link that hides it. Returns the address, or None.

        results_url is the address of the results page in the window, which holds the link. The link is
        clicked there, as a person would click it. (With the lookup "request", Google's link is first
        asked with a plain web request, which needs no browser at all.) Only if the link cannot be
        clicked is its address opened in a second tab. domain is the domain that was searched for.
        """
        if self.gone or self.lookups_off or "google" in self.skipped:
            return None
        if link in self._resolved:
            return self._resolved[link]

        destination = None
        if self.lookup == "request" and self._requests_refused < 2:
            destination = self._request_lookup(link)
            if not destination:
                self._pause()
        if not destination:
            destination = self._click_lookup(link, results_url, domain) if results_url else _NOT_OPENED
            if destination is _NOT_OPENED:
                destination = None
                if not self.gone:
                    if results_url:
                        if not self._said_not_clickable:
                            self._said_not_clickable = True
                            self.log(
                                "[!] A result could not be clicked in the results page (%s). Its link is opened in a "
                                "second tab instead. If something in the window covers the results, close it."
                                % self._click_problem
                            )
                        self._pause()
                    destination = self._tab_lookup(link, domain)

        if self.gone or "google" in self.skipped:
            return None
        if destination:
            self._unresolved_in_a_row = 0
            self._resolved[link] = destination
        elif not self.lookups_off:
            self._unresolved_in_a_row += 1
            if self._unresolved_in_a_row >= 3:
                self.log(
                    "[!] Three Google results in a row did not show where they lead. Results that hide "
                    "their address are left out for the rest of this run."
                )
                self.lookups_off = True
        return destination

    def _pause(self):
        """The wait between two requests to the same search engine."""
        if self.delay > 0:
            time.sleep(self.delay)

    def _outside(self, link):
        """A function that gives an address back if it is not one of the search engine's own."""
        own = (_host(link),)

        def outside(address):
            kind, value = links.classify_google_link(address or "", own)
            return value if kind == "address" else None

        return outside

    def _request_lookup(self, link):
        """Ask the search engine's link with plain web requests and read where it redirects, without
        going there. Returns the address, or None if the search engine did not give one."""
        outside = self._outside(link)
        address, problem = link, "too many redirects"
        try:
            for _ in range(5):  # the link may first lead to another of the search engine's own addresses
                status, target = web.redirect_target(address, self.user_agent)
                if not target:
                    problem = "HTTP %d and no redirect" % status
                    break
                if outside(target):
                    self._requests_refused = 0
                    return outside(target)
                address = target
        except web.FetchError as error:
            problem = str(error)
        self._requests_refused += 1
        if self._requests_refused >= 2:
            self.log(
                "[!] Google does not tell plain requests where its result links lead (%s). "
                "The results are clicked in the browser window instead." % problem
            )
        return None

    def _click_lookup(self, link, results_url, domain=None):
        """Click the result in the results page and read from the network log where the click leads.
        Returns the address, None, or _NOT_OPENED if the link could not be clicked."""
        driver = self.driver
        self._click_problem = "it is not shown there"
        try:
            results_tab = driver.current_window_handle
            before = set(driver.window_handles)
            # The results page as it is in the window now: its address (a search engine may have changed
            # the one that was opened) and the page itself
            shown = driver.current_url
            page = driver.find_element("tag name", "html")
            element = driver.execute_script(_FIND_LINK, link)
            if element is None:
                return _NOT_OPENED
            # The click is known by where it happens: the next page this window asks for. Its address may
            # differ from the one that was read (a page can change a link at the moment it is clicked).
            try:
                frame = driver.execute_cdp_cmd("Page.getFrameTree", {})["frameTree"]["frame"]["id"]
            except (self._errors, KeyError, TypeError):
                frame = None
            driver.get_log("performance")  # discard what was logged before
            driver.set_page_load_timeout(LOOKUP_WAIT)
        except self._errors as error:
            self._click_problem = _first_line(error)
            return None if self._failed(error) else _NOT_OPENED

        def left_results():
            """True once the window holds another page than the results."""
            try:
                page.is_enabled()
                return False
            except self._stale:
                return True

        def open_again():
            try:
                driver.get(link)
            except self._errors:
                pass

        destination, restore = None, True
        try:
            self._click_problem = "the click did not open it"
            try:
                element.click()
            except self._errors as error:
                # Covered by something, or the page it leads to is slow: the log says what happened
                self._click_problem = _first_line(error)
            destination = self._watch(link, left_results, open_again, frame, domain)
        except self._errors:
            destination = None
        except BaseException:
            # Ctrl+C: nothing more is asked of the search engine on the way out
            restore = False
            raise
        finally:
            self._back_to_results(results_tab, before, results_url if restore else None, shown, page)
        return destination

    def _tab_lookup(self, link, domain=None):
        """Open the address of a result link in a second tab and read from the network log where it
        leads. Returns the address, or None."""
        driver = self.driver
        try:
            results_tab = driver.current_window_handle
            before = set(driver.window_handles)
        except self._errors as error:
            self._failed(error)
            return None

        def open_link():
            try:
                driver.get(link)
            except self._errors:
                pass  # a file that is not shown, a page that takes too long: the log is what counts

        destination = None
        try:
            driver.switch_to.new_window("tab")
            driver.get_log("performance")  # discard what was logged before
            driver.set_page_load_timeout(LOOKUP_WAIT)
            open_link()
            destination = self._watch(link, None, open_link, None, domain)
        except self._errors:
            destination = None
        finally:
            self._back_to_results(results_tab, before)
        return None if destination is _NOT_OPENED else destination

    def _watch(self, link, left_results=None, open_again=None, frame=None, domain=None):
        """Wait for the link that was just opened in the current window to show where it leads.

        left_results and frame are given when the link was clicked in the results page: a function that
        says whether the window has left that page, and the window's own frame in the network log.
        open_again opens the link once more. domain is the domain that was searched for.

        The first address outside the search engine that the window asks for after the link is the one:
        through a redirect, or because the page sent the browser on by itself. Where the site sends the
        browser from there is not what the search engine listed. If the search engine shows a page of
        its own instead (a check, a button that has to be pressed), the person is asked to deal with it,
        and has all the time that takes. Returns the address, None, or _NOT_OPENED if the browser never
        asked for the link.
        """
        driver = self.driver
        start = _page(link)  # the network log leaves the "#..." part out
        own_host = _host(link)
        outside = self._outside(link)
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

        def in_log():
            """(whether the browser asked for the link, the address that led to or None)"""
            link_frame, opened = None, False
            for document_frame, address in documents:
                if not opened:
                    # The link is known by its address or, when it was clicked, by being the first page
                    # the results window asks for
                    if _page(address) == start or (frame is not None and document_frame == frame):
                        link_frame, opened = document_frame, True
                        if outside(address):
                            return True, outside(address)
                elif document_frame == link_frame and outside(address):
                    return True, outside(address)
            return opened, None

        started = time.time()
        deadline = started + LOOKUP_WAIT
        shown_since = None
        checks, asked, dealt_with, opened_again = 0, False, False, False
        while True:
            read_log()
            opened, destination = in_log()
            if destination:
                return destination
            now = driver.current_url
            if outside(now):
                return outside(now)  # the window went on by itself and the log did not show it

            # Is the window showing a page of the search engine other than the results? (A check can be
            # on another of the search engine's hosts than the link.)
            own_page = now.lower().startswith(("http://", "https://")) and (_host(now) == own_host or not outside(now))
            if own_page and (left_results is None or left_results()):
                source = driver.page_source
                check = page_state("google", now, source) == "check"
                if not check and domain:
                    # A notice in between that names the address ("you are being sent to ..."). Only an
                    # address on the domain that was searched for can be the result's.
                    hrefs = driver.execute_script(_NOTICE_LINKS)
                    named = set(filter(None, (outside(href) for href in hrefs or [] if isinstance(href, str))))
                    if len(named) == 1 and links.is_on_domain(next(iter(named)), domain):
                        return named.pop()
                if shown_since is None and driver.execute_script("return document.readyState") == "complete":
                    shown_since = time.time()
                # A page that says it will send the browser on by itself is given the time to do so
                waited = shown_since is not None and time.time() - shown_since >= SHOWN_WAIT and not _FORWARDS.search(source)
                if check or (waited and not asked):
                    if check and checks >= 3:
                        self.log("[!] Google still shows a check. It is left out for the rest of this run.")
                        self.skipped.add("google")
                        return None
                    checks += 1 if check else 0
                    asked = True
                    what = "a check" if check else "a page of its own instead of the result"
                    answer = self._answer(
                        "[?] Google is showing %s in the browser window. If something has to be done there "
                        "(a check, a Continue button), do it, then press Enter here. Type n and Enter to stop "
                        "reading the addresses of results, or s and Enter to skip Google. " % what,
                        unattended="s" if check else "n",
                    )
                    if answer.startswith("s"):
                        self._skip("google")
                        return None
                    if answer.startswith("n"):
                        self.log("[*] Results that hide their address are left out for the rest of this run.")
                        self.lookups_off = True
                        return None
                    deadline, shown_since = time.time() + LOOKUP_WAIT, None
                    read_log()
                    still_checked = page_state("google", driver.current_url, driver.page_source) == "check"
                    if not in_log()[1] and still_checked and open_again is not None:
                        # The window still shows the check: open the link again to see whether it is still asked for
                        open_again()
                    elif check:
                        dealt_with = True
                    continue
            else:
                shown_since = None

            if not opened and time.time() - started >= CLICK_WAIT:
                return _NOT_OPENED
            if time.time() >= deadline:
                if dealt_with and open_again is not None and not opened_again:
                    # The check is out of the way, but the browser was not sent on: open the link again
                    opened_again, deadline = True, time.time() + LOOKUP_WAIT
                    open_again()
                    continue
                return None
            time.sleep(0.3)

    def _back_to_results(self, results_tab, before, results_url=None, shown=None, page=None):
        """Close every window a lookup opened and put the results page back in the window. Nothing here
        may get in the way of an error that is already on its way up.

        results_url is the search that was opened, shown the address the window gave the results page
        before the lookup, and page that page itself. Without them the window is left as it is.
        """
        driver = self.driver

        def patiently(action, seconds=3):
            """Run a browser command. A dialog on the page (alert, confirm) makes a command fail and is
            put away by that, so the command is tried again for a while. Returns (done, result)."""
            deadline = time.time() + seconds
            while True:
                try:
                    return True, action()
                except Exception as error:
                    if "alert" not in error.__class__.__name__.lower() or time.time() >= deadline:
                        return False, None

        def close_others(keep):
            for attempt in range(4):
                try:
                    extra = [handle for handle in driver.window_handles if handle not in keep]
                    if not extra:
                        return
                    for handle in extra:
                        if attempt % 2 == 0:
                            # Asked of the browser itself: a page that keeps putting up dialogs cannot stop this
                            driver.execute_cdp_cmd("Target.closeTarget", {"targetId": handle.replace("CDwindow-", "")})
                        else:
                            driver.switch_to.window(handle)
                            driver.close()
                except Exception:
                    pass
                time.sleep(0.1)

        def on_results():
            # The surest sign is that the page itself is still there; after going back it is its address
            if patiently(page.is_enabled)[0]:
                return True
            done, now = patiently(lambda: driver.current_url)
            return done and _page(now) == _page(shown)

        try:
            driver.switch_to.window(results_tab)
        except Exception:
            pass
        close_others(before)
        try:
            driver.switch_to.window(results_tab)
            driver.set_page_load_timeout(PAGE_LOAD_WAIT)
        except Exception:
            self._lose()
            return
        if not results_url or on_results():
            return

        # The click took the window to a page (not a file). Going back shows the results as they were.
        for _ in range(3):
            patiently(driver.back)
            if on_results():
                return

        # Only opening the search again brings the results back, and their links may be new ones. This
        # is done in a new tab, and the page that would not let go is closed with its tab.
        self.results_reloaded = True
        done, _ = patiently(lambda: driver.switch_to.new_window("tab"))
        if not done:
            # A page that puts up dialogs without end takes every command sent to its window
            self._lose()
            return
        try:
            fresh = driver.current_window_handle
            close_others({fresh})
            driver.switch_to.window(fresh)
            driver.set_page_load_timeout(PAGE_LOAD_WAIT)
        except Exception:
            self._lose()
            return
        self._deny_downloads()
        self._go(results_url)


def _keep(session, engine, candidates, domain, file_type, found):
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
                session.keep(link, engine)
    return new, on_domain


def search_google(session, domain, file_type, max_pages=5, delay=2):
    """Search Google for "site:domain filetype:type" in the browser window."""
    log = session.log
    found = []
    query = "site:%s filetype:%s" % (domain, file_type)
    own_hosts = (_host(GOOGLE_URL),)
    unread = 0

    def read():
        addresses, hidden = [], []
        for href in session.hrefs():
            kind, value = links.classify_google_link(href, own_hosts)
            if kind == "address":
                addresses.append(value)
            elif kind == "redirect" and value not in hidden:
                hidden.append(value)
        return addresses, hidden

    def in_the_way():
        # Not a results page at all: something this program does not know has to be dealt with first
        return not any(read()) and not has_search_box(session.source())

    log("[*] Searching Google for '%s' in the browser" % query)
    for page_number in range(1, max_pages + 1):
        if page_number > 1 and delay > 0:
            time.sleep(delay)
        log("[*] Now Analyzing page %d of Google search results" % page_number)
        url = GOOGLE_URL + "?" + urllib.parse.urlencode({"q": query, "start": (page_number - 1) * 10, "filter": "0"})
        if not session.show("google", url):
            break

        addresses, hidden = read()
        if not addresses and not hidden:
            if not has_search_box(session.source()):
                if not session.clear_check("google", url, in_the_way):
                    break
                addresses, hidden = read()
            elif page_number == 1 and session.second_look("google", url):
                addresses, hidden = read()
        new, _ = _keep(session, "google", addresses, domain, file_type, found)

        # Google's result links do not show where they lead: each result is clicked to find out
        if hidden and not session.lookups_off:
            how = "each is clicked in the browser window"
            if session.lookup == "request" and session._requests_refused < 2:
                how = "with plain requests to Google's links"
            log("[*] Reading where %d results lead (%s)" % (len(hidden), how))
        position = 0
        while position < len(hidden):
            if session.lookups_off or session.gone or "google" in session.skipped:
                unread += len(hidden) - position
                break
            # Reading an address is a request to the search engine like any other
            if delay > 0 and hidden[position] not in session._resolved:
                time.sleep(delay)
            destination = session.resolve(hidden[position], url, domain)
            if destination:
                new += _keep(session, "google", [destination], domain, file_type, found)[0]
            else:
                unread += 1
            position += 1
            if session.results_reloaded:
                # The results had to be opened again, and may now carry new links for the same results
                session.results_reloaded = False
                if not session.clear_check("google", url):
                    break
                again = read()[1]
                if len(again) == len(hidden):
                    hidden = again

        if session.gone or "google" in session.skipped:
            break
        # No result links at all, or nothing new: this was the last page
        if (not addresses and not hidden) or new == 0:
            break

    if unread:
        log("[!] The address behind %d Google results was not read." % unread)
    log(sources.tally("Google (browser)", len(found), "%s files" % file_type))
    return found


def bing_knows_site(session, domain):
    """Ask Bing for the site alone before asking for file types.

    Bing answers some searches with pages that have nothing to do with them: when it does not know the
    site, and when it takes the search for an automated one. A search for "site:domain" must return pages
    of that domain; if it returns none, the searches for file types would only bring up other sites.
    Returns False (after saying so) if Bing is to be left out.
    """
    log = session.log
    query = "site:%s" % domain
    log("[*] Asking Bing in the browser for '%s', to see whether it keeps to the site" % query)
    url = BING_URL + "?" + urllib.parse.urlencode({"q": query})
    if not session.show("bing", url):
        return False
    results = links.count_bing_results(session.source())
    if results == 0 and session.second_look("bing", url):
        results = links.count_bing_results(session.source())
    if session.gone or "bing" in session.skipped:
        return False
    results, on_domain, keeps_to_site = links.bing_keeps_to_site(session.source(), domain)
    if keeps_to_site:
        return True
    if results:
        log(sources.BING_OTHER_SITES % (query, on_domain, results))
    else:
        log(sources.BING_NO_RESULTS % query)
    session.skipped.add("bing")
    return False


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
        new, on_domain = _keep(session, "bing", [links.unwrap_bing(href) for href in session.hrefs()], domain, file_type, found)
        results_seen += results
        on_domain_seen += on_domain
        if results == 0 or new == 0:
            break
        first += results

    if results_seen > 0 and on_domain_seen == 0:
        log(sources.BING_NO_FILES % (query, file_type))
    log(sources.tally("Bing (browser)", len(found), "%s files" % file_type))
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
            new, _ = _keep(session, "duckduckgo", [links.unwrap_duckduckgo(href) for href in session.hrefs()], domain, file_type, found)
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

    log(sources.tally("DuckDuckGo (browser)", len(found), "%s files" % file_type))
    return found


ENGINES = {"google": search_google, "bing": search_bing, "duckduckgo": search_duckduckgo}


def search(engines, domain, file_types, max_pages=5, delay=2, browser_name="chrome", log=print, ask=input,
           on_link=None, lookup="click", user_agent=None, tallies=None):
    """Run the searches of every named engine and file type in one browser window. Returns the links found.

    on_link, if given, is called with each link as soon as it is found, so that the caller has what was
    found when the search ends early. Ctrl+C is passed on to the caller, after the browser has been closed.
    lookup says how the address of a Google result that hides it is read: "click" (the result is clicked
    in the window), "request" (Google's link is asked with a plain web request first) or "off".
    tallies, if given, is a list that gets one (name, number of files or a remark) entry per search engine.
    """
    def tally(engine, count, stopped):
        if tallies is not None:
            tallies.append((NAMES[engine] + " (browser)", sources.tally_value(count, stopped)))

    try:
        session = Session(
            browser_name, log, ask, network_log="google" in engines, on_link=on_link, lookup=lookup, user_agent=user_agent
        )
    except BrowserUnavailable as error:
        log("[!] The browser search was not done: %s" % error)
        for engine in engines:
            tally(engine, 0, True)
        return []

    log("[*] A browser window has been opened. If a search engine shows a check there, you will be asked to deal with it.")
    session.delay = delay
    through = set()  # the search engines that were asked for every file type
    try:
        for engine in engines:
            if session.gone:
                break
            # Bing is first asked for the site alone: see bing_knows_site
            if engine == "bing" and not bing_knows_site(session, domain):
                continue
            first = engine != "bing"
            for file_type in file_types:
                if session.gone or engine in session.skipped:
                    break
                if not first and delay > 0:
                    time.sleep(delay)
                first = False
                ENGINES[engine](session, domain, file_type, max_pages, delay)
            else:
                if not session.gone and engine not in session.skipped:
                    through.add(engine)
    except KeyboardInterrupt:
        raise
    except Exception as error:
        # Selenium or the browser failed in a way that was not foreseen: keep what was found
        log("[!] The browser search stopped early (%s). What it had found is kept." % _first_line(error))
    finally:
        for engine in engines:
            tally(engine, len(session.per_engine.get(engine, ())), engine not in through)
        session.close()
    return session.found
