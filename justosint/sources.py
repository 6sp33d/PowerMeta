"""Where file links come from when plain web requests are enough: the Internet Archive's index,
Bing, and DuckDuckGo's script-free page.

The search engines turn script requests away on many networks. Each function says so when that
happens instead of quietly returning nothing.
"""

from __future__ import annotations

import time
import urllib.parse

from . import links
from .web import FetchError

# Where each source is asked
BING_URL = "https://www.bing.com/search"
DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"
ARCHIVE_INDEX_URL = "https://web.archive.org/cdx/search/cdx"


def tally(source, count, what="files"):
    """The line that says what a source gave: "[+] Bing: 3 pdf files found", "[+] Bing: 1 pdf file found"
    or "[-] Bing: no pdf files found"."""
    if count == 1 and what.endswith("files"):
        what = what[:-1]
    if count:
        return "[+] %s: %d %s found" % (source, count, what)
    return "[-] %s: no %s found" % (source, what)


def tally_row(source, count, stopped=False, remark="", unanswered=()):
    """What a source gave, for the summary of a run: (name, number of files, remark). The remark is empty
    unless the source did not get through.

    A source that was stopped (it was left out, it refused and was skipped, or the search was cut short)
    is marked "stopped early", or "left out" if nothing had come of it by then. For a source that was
    asked to the end, unanswered says for each of its searches what was left unanswered ("" nothing,
    "all" of it, "part" of it): the remark is "did not answer" when not one search was answered and
    "incomplete" when some of it was not.
    """
    if not remark:
        unanswered = list(unanswered)
        if stopped:
            remark = "stopped early" if count else "left out"
        elif unanswered and all(entry == "all" for entry in unanswered):
            remark = "did not answer"
        elif any(unanswered):
            remark = "incomplete"
    return (source, count, remark)


def _collect(page_links, unwrap, domain, file_type, found):
    """Unwrap each link, keep the wanted files that are new, and count links on the domain."""
    new, on_domain = 0, 0
    for href in page_links:
        link = unwrap(href)
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


def search_archive(fetcher, domain, file_types, max_results=10000, log=print):
    """Ask the Internet Archive's Wayback Machine index for the addresses it has captured on the domain
    and its sub-domains that name a wanted file type (at most max_results of them).

    The index is a service made to be queried by programs, and one request covers all file types.
    Files it lists may have been removed from the site since. Returns (a list of links.ArchiveEntry,
    remark): the remark is empty, "did not answer", or "incomplete" when the index holds more than was read.
    """
    log("[*] Asking the Internet Archive which files it has seen on %s" % domain)
    query = [
        ("url", domain),
        ("matchType", "domain"),
        ("fl", "original,timestamp"),
        ("collapse", "urlkey"),
        ("gzip", "false"),
        ("filter", "statuscode:200"),
        ("filter", links.archive_filter(file_types)),
        ("limit", str(max_results)),
    ]
    try:
        page = fetcher.get(ARCHIVE_INDEX_URL + "?" + urllib.parse.urlencode(query), timeout=180)
    except FetchError as error:
        log("[!] The Internet Archive did not answer (%s). Its index is sometimes slow or busy; try again later." % error)
        return [], "did not answer"

    entries = links.parse_archive_index(page.text, domain, file_types)
    log(tally("Internet Archive", len(entries)))

    # The index stops at the limit without saying so
    if len([line for line in page.text.split("\n") if line.strip()]) >= max_results:
        log(
            "[!] The Internet Archive has more than %d matching addresses for %s. Only the first %d were read, "
            "so this list is incomplete." % (max_results, domain, max_results)
        )
        return entries, "incomplete"
    return entries, ""


# What is said about Bing's answers. Each is two short lines rather than one long one.
BING_OTHER_SITES = (
    "[!] Bing answered '%s' with pages of other sites (%d of its %d results are on the site).\n"
    "[!] It does that with searches it takes for automated, or for a site it does not know. "
    "Nothing of it is kept; Bing is left out for this run."
)
BING_NO_RESULTS = (
    "[-] Bing returned no results for '%s': it has no pages of the site, or it showed a check instead.\n"
    "[!] Bing is left out for this run."
)
BING_NO_FILES = "[-] Bing showed only other sites for '%s': it has no %s files of the site, or has stopped keeping to it."


def bing_knows_site(fetcher, domain, log=print):
    """Ask Bing for the site alone before asking for file types.

    Bing answers some searches with pages that have nothing to do with them: when it does not know the
    site, and when it takes the search for an automated one. A search for "site:domain" must return pages
    of that domain; if it returns none, the searches for file types would only bring up other sites.
    Returns False (after saying so) if Bing is to be left out.
    """
    query = "site:%s" % domain
    log("[*] Asking Bing for '%s', to see whether it keeps to the site" % query)
    try:
        page = fetcher.get(BING_URL + "?" + urllib.parse.urlencode({"q": query}))
    except FetchError as error:
        log("[!] Bing did not answer (%s). It is left out for the rest of this run." % error)
        return False
    results, on_domain, keeps_to_site = links.bing_keeps_to_site(page.text, domain)
    if keeps_to_site:
        return True
    if results:
        log(BING_OTHER_SITES % (query, on_domain, results))
    else:
        log(BING_NO_RESULTS % query)
    return False


def search_bing(fetcher, domain, file_type, max_pages=5, delay=2, log=print):
    """Search Bing for "site:domain filetype:type".

    Returns (links, unanswered): unanswered is "" when Bing answered every request, "all" when it did
    not answer the search at all, and "part" when it stopped answering at a later page.
    """
    found = []
    query = "site:%s filetype:%s" % (domain, file_type)
    first, results_seen, on_domain_seen = 1, 0, 0
    unanswered = ""

    log("[*] Searching Bing for '%s'" % query)
    for page_number in range(1, max_pages + 1):
        if page_number > 1 and delay > 0:
            time.sleep(delay)
        log("[*] Now Analyzing page %d of Bing search results" % page_number)
        try:
            page = fetcher.get(BING_URL + "?" + urllib.parse.urlencode({"q": query, "first": first}))
        except FetchError as error:
            log("[!] Bing did not answer for page %d: %s" % (page_number, error))
            unanswered = "all" if page_number == 1 else "part"
            break

        results = links.count_bing_results(page.text)
        new, on_domain = _collect(links.links_from_html(page.text), links.unwrap_bing, domain, file_type, found)
        results_seen += results
        on_domain_seen += on_domain

        # No results, or nothing new: this was the last page
        if results == 0 or new == 0:
            break
        first += results

    # Bing fills the page with other sites when it has nothing on the site for a search
    if results_seen > 0 and on_domain_seen == 0:
        log(BING_NO_FILES % (query, file_type))
    log(tally("Bing", len(found), "%s files" % file_type))
    return found, unanswered


def search_duckduckgo(fetcher, domain, file_type, max_pages=5, delay=2, log=print):
    """Search DuckDuckGo's script-free page for "site:domain filetype:type".

    DuckDuckGo wants the query as a form post, and gives each results page a "Next" form holding what to
    post for the following page. Returns (links, blocked, unanswered): blocked is True when DuckDuckGo
    refused to answer (its bot check, or an error), in which case it should be left alone for the rest of
    the run. unanswered is "" when every request was answered with results, "all" when the search was not
    answered at all, and "part" when a later page was not.
    """
    found = []
    query = "site:%s filetype:%s" % (domain, file_type)
    fields = {"q": query, "b": ""}
    offset, results_seen, on_domain_seen = 0, 0, 0
    blocked = False
    unanswered = ""

    log("[*] Searching DuckDuckGo for '%s'" % query)
    for page_number in range(1, max_pages + 1):
        if page_number > 1 and delay > 0:
            time.sleep(delay)
        log("[*] Now Analyzing page %d of DuckDuckGo search results" % page_number)
        try:
            page = fetcher.post(DUCKDUCKGO_URL, fields)
        except FetchError as error:
            if page_number == 1:
                log("[!] DuckDuckGo did not answer the search (%s). It is skipped for the rest of this run." % error)
                blocked = True
            else:
                log("[!] DuckDuckGo did not answer for page %d (%s). The results so far are kept." % (page_number, error))
            unanswered = "all" if page_number == 1 else "part"
            break

        if links.is_duckduckgo_bot_check(page.text):
            log(
                "[!] DuckDuckGo answered with its bot check instead of results. It does this to automated "
                "requests from many networks; DuckDuckGo is skipped for the rest of this run."
            )
            blocked = True
            unanswered = "all" if page_number == 1 else "part"
            break

        results = links.count_duckduckgo_results(page.text)
        new, on_domain = _collect(links.links_from_html(page.text), links.unwrap_duckduckgo, domain, file_type, found)
        results_seen += results
        on_domain_seen += on_domain

        # Nothing new: this was the last page
        if new == 0:
            break

        next_form = links.duckduckgo_next_form(page.text, after=offset)
        if next_form is None:
            break
        fields = next_form
        offset = int(next_form["s"])

    if results_seen > 0 and on_domain_seen == 0:
        log("[!] DuckDuckGo returned results, but none of them are on %s." % domain)
    log(tally("DuckDuckGo", len(found), "%s files" % file_type))
    return found, blocked, unanswered
