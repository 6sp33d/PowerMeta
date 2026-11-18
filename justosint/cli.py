"""The command line: search, download, extract."""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
import time

from . import BANNER, NAME, __version__, exif, links, sources
from .web import DEFAULT_USER_AGENT, PROGRAM_USER_AGENT, FetchError, Fetcher, SaveError, download

SOURCES = ("archive", "bing", "duckduckgo")
BROWSER_ENGINES = ("google", "bing", "duckduckgo")
SOURCE_NAMES = {"archive": "Internet Archive", "bing": "Bing", "duckduckgo": "DuckDuckGo", "google": "Google"}

# Control characters, and the characters that reverse the direction of text
_UNPRINTABLE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def log(message=""):
    # Much of what is printed comes from outside: addresses, metadata, the error text of a server.
    # None of it may steer the terminal, so control characters are shown as "?".
    print(_UNPRINTABLE.sub("?", str(message)), flush=True)


def counted(count, noun="file", verb=False):
    """A number with its noun: "1 file", "3 files". With verb: "1 file was", "3 files were"."""
    text = "%d %s" % (count, noun if count == 1 else noun + "s")
    if verb:
        text += " was" if count == 1 else " were"
    return text


def log_settings(rows):
    """Lines of the form "[*] Target domain : example.com", with the colons under each other."""
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        log("[*] %-*s : %s" % (width, name, value))


def log_table(title, rows, numbers=False, indent=4):
    """A title, and under it one indented line per row with the values in a column of their own.
    numbers=True puts the values against the right edge of their column, as in a table of counts."""
    log(title)
    if not rows:
        return
    names = max(len(str(name)) for name, _ in rows)
    values = max(len(str(value)) for _, value in rows)
    for name, value in rows:
        if numbers:
            log("%s%-*s  %*s" % (" " * indent, names, name, values, value))
        else:
            log("%s%-*s : %s" % (" " * indent, names, name, value))


def files_by_type(file_links, file_types):
    """How many of the links there are of each wanted file type: a list of (type, number), in the order
    the types were asked for. Links of no wanted type (a list of links can hold them) come last, as "other"."""
    counts = dict.fromkeys(file_types, 0)
    other = 0
    for link in file_links:
        file_type = links.file_type_of(link, file_types)
        if file_type is None:
            other += 1
        else:
            counts[file_type] += 1
    rows = [(file_type, counts[file_type]) for file_type in file_types]
    if other:
        rows.append(("other", other))
    return rows


def log_summary(subject, rows, by_type=(), by_source=(), files=0):
    """The closing summary of a run, made to fit in one screenshot: a few "name : value" rows, then how
    many files there are of each type, then how many each source gave.

    by_source holds (name, number of files, remark) and files is the number of different files found.
    """
    log_table("[*] Summary for %s" % subject, rows)
    if by_type:
        log()
        log_table("    Files by type", by_type, numbers=True, indent=8)
    if by_source:
        log()
        log("    Files by source")
        names = max(len(name) for name, _, _ in by_source)
        numbers = max(len(str(count)) for _, count, _ in by_source)
        for name, count, remark in by_source:
            log("        %-*s  %*d%s" % (names, name, numbers, count, "  (%s)" % remark if remark else ""))
        # Two sources often know the same file, so their numbers can add up to more than there are files
        if sum(count for _, count, _ in by_source) > files:
            log("        (a file that several sources gave is counted for each of them)")


def ask_yes_no(question):
    """Ask a yes/no question. Without a terminal nothing can be asked, and the answer is no."""
    if sys.stdin is None or not sys.stdin.isatty():
        log("[?] %s Not asked, because there is no terminal: answered no." % question)
        return False
    while True:
        try:
            answer = input("[?] " + question + " [Y/n] ").strip().lower()
        except EOFError:
            return False
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def ask_person(question):
    """Put a question of the browser search to the person at the keyboard. Without a terminal nobody can
    be asked: that is said, and EOFError tells the caller to go on without an answer."""
    if sys.stdin is None or not sys.stdin.isatty():
        log("%sNot asked, because there is no terminal." % question)
        raise EOFError
    return input(question)


def _name_list(text, allowed, option, parser):
    names = []
    for entry in str(text).split(","):
        name = entry.strip().lower()
        if not name:
            continue
        if name not in allowed:
            parser.error("%s: '%s' is not one of %s" % (option, entry.strip(), ", ".join(allowed)))
        if name not in names:
            names.append(name)
    return names


def find_files(domain, file_types, http_sources, browser_engines, user_agent=None, max_pages=5, delay=2,
               browser_name="chrome", google_lookup="click"):
    """Ask every chosen source.

    Returns (sorted links, {link: address of the Internet Archive's copy}, interrupted, tallies).
    interrupted is True if Ctrl+C ended the search early; what had been found by then is still returned.
    tallies says for each source what it gave: a list of (name, number of files, remark), where the
    remark tells of a source that did not get through ("left out", "stopped early", "did not answer",
    "incomplete": see sources.tally_row).
    """
    found, by_key, archive_copies = [], {}, {}

    def keep(link):
        # Links are compared without "http://" or "https://": the Internet Archive often knows a file
        # under its old http address while a search engine gives today's https one
        key = links.link_key(link)
        if key not in by_key:
            by_key[key] = link
            found.append(link)
        return by_key[key]

    archive_entries, archive_remark, archive_asked = [], "", False
    from_archive, from_bing, from_duckduckgo = set(), set(), set()
    left_out = set()
    # For each search of a search engine that has ended, what it left unanswered ("", "all" or "part").
    # An engine was asked to the end when there is an entry for every file type.
    unanswered = {"bing": [], "duckduckgo": []}
    browser_tallies = []
    interrupted = False
    # An engine that is searched through the browser is not also asked with plain requests
    engines = [name for name in ("bing", "duckduckgo") if name in http_sources and name not in browser_engines]
    asked = list(engines)
    try:
        # The Internet Archive answers for all file types in a single request. Its index is a service
        # for programs, so it is told who is asking unless another User-Agent was asked for.
        if "archive" in http_sources:
            archive_entries, archive_remark = sources.search_archive(
                Fetcher(user_agent or PROGRAM_USER_AGENT), domain, file_types, log=log
            )
            archive_asked = True

        # One session per search engine, so that the cookies it sets are sent back as a browser would
        bing = Fetcher(user_agent or DEFAULT_USER_AGENT)
        duckduckgo = Fetcher(user_agent or DEFAULT_USER_AGENT)
        duckduckgo_blocked = False
        first = True
        # Bing is first asked for the site alone. If it does not keep to the site, it is not asked for files.
        if "bing" in engines:
            first = False
            if not sources.bing_knows_site(bing, domain, log):
                engines.remove("bing")
                left_out.add("bing")
        for file_type in file_types:
            ask_bing = "bing" in engines
            ask_duckduckgo = "duckduckgo" in engines and not duckduckgo_blocked
            if not ask_bing and not ask_duckduckgo:
                break
            # The pause also applies between the last request for one file type and the first for the next
            if not first and delay > 0:
                time.sleep(delay)
            first = False

            if ask_bing:
                result, gap = sources.search_bing(bing, domain, file_type, max_pages, delay, log)
                for link in result:
                    from_bing.add(keep(link))
                unanswered["bing"].append(gap)
            if ask_duckduckgo:
                # DuckDuckGo is left alone for the rest of the run once it has refused to answer
                result, duckduckgo_blocked, gap = sources.search_duckduckgo(duckduckgo, domain, file_type, max_pages, delay, log)
                for link in result:
                    from_duckduckgo.add(keep(link))
                unanswered["duckduckgo"].append(gap)
                if duckduckgo_blocked:
                    left_out.add("duckduckgo")

        if browser_engines:
            from . import browser

            # Each link is handed over as soon as it is found, so that nothing is lost if the search is cut short
            found_in_browser = browser.search(
                browser_engines, domain, file_types, max_pages, delay, browser_name, log, ask=ask_person,
                on_link=keep, lookup=google_lookup, user_agent=user_agent, tallies=browser_tallies,
            )
            for link in found_in_browser:
                keep(link)
    except KeyboardInterrupt:
        interrupted = True
        log()
        log("[!] Interrupted: the search stops here. What was found so far is kept.")

    # The Archive's files are added last, so that for a file both know the search engine's current
    # address is the one kept. Either way the Archive's copy is remembered for it.
    for entry in archive_entries:
        kept = keep(entry.link)
        from_archive.add(kept)
        archive_copies.setdefault(kept, entry.archive_copy)

    # Every source is counted by the different files it gave. A source that was not asked to the end
    # (left out, or cut short by Ctrl+C) is marked as such, and so is one that left requests unanswered.
    tallies = []
    if "archive" in http_sources:
        tallies.append(sources.tally_row(SOURCE_NAMES["archive"], len(from_archive), not archive_asked, archive_remark))
    for name, links_found in (("bing", from_bing), ("duckduckgo", from_duckduckgo)):
        if name in asked:
            stopped = name in left_out or len(unanswered[name]) < len(file_types)
            tallies.append(sources.tally_row(SOURCE_NAMES[name], len(links_found), stopped, unanswered=unanswered[name]))
    if browser_engines and not browser_tallies:
        # The search was cut short before the browser search had begun
        browser_tallies = [sources.tally_row(SOURCE_NAMES[name] + " (browser)", 0, True) for name in browser_engines]
    tallies.extend(browser_tallies)
    return sorted(found), archive_copies, interrupted, tallies


def download_files(file_links, output_dir, user_agent=None, archive_copies=None, file_types=None):
    """Download every link into output_dir. Returns (downloaded, failed, interrupted).

    file_types are the wanted file types: every saved file gets a name that ends in one of them.
    interrupted is True if Ctrl+C ended the downloads early; the files saved by then are whole and are kept.
    """
    file_links = [link.strip() for link in file_links if link and link.strip()]
    file_types = file_types or links.DEFAULT_FILE_TYPES
    if not file_links:
        log("[-] There are no files to download.")
        return 0, 0, False

    if not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    log("[*] Downloading %s to %s" % (counted(len(file_links)), os.path.abspath(output_dir)))

    downloaded = failed = 0
    interrupted = False
    try:
        for link in file_links:
            # When downloading files if a file has the same name a number is added to it
            name = links.safe_file_name(link, file_types)
            candidate, number = name, 1
            while os.path.exists(os.path.join(output_dir, candidate)):
                base, extension = os.path.splitext(name)
                candidate = "%s-%d%s" % (base, number, extension)
                number += 1
            destination = os.path.join(output_dir, candidate)

            log("[*] Now Downloading %s" % link)
            problem = None
            try:
                try:
                    # Target servers often have expired or self-signed certificates, so certificate errors are
                    # ignored for them - and only for them
                    download(link, destination, user_agent or DEFAULT_USER_AGENT, verify_certificate=False)
                except FetchError as error:
                    problem = str(error)

                # A file that is gone from the site may still be in the Internet Archive
                if problem and archive_copies and link in archive_copies:
                    log("[*] Not available from the site (%s). Trying the copy in the Internet Archive." % problem)
                    time.sleep(1)
                    try:
                        download(archive_copies[link], destination, user_agent or PROGRAM_USER_AGENT, verify_certificate=True)
                        problem = None
                    except FetchError as error:
                        problem = str(error)
            except SaveError as error:
                # This computer cannot store the file (disk full, folder not writable). The next file would
                # fare no better, and neither the site nor the Internet Archive has anything to do with it.
                left = len(file_links) - downloaded - failed
                log("[!] %s. The downloads stop here: %s not downloaded." % (error, counted(left, verb=True)))
                failed += left
                break

            if problem:
                log("[!] Could not download %s : %s" % (link, problem))
                failed += 1
            else:
                downloaded += 1
    except KeyboardInterrupt:
        interrupted = True
        log()
        log("[!] Interrupted: the downloads stop here. What was downloaded so far is kept.")
    log("%s Downloaded %s to %s (%d failed)." % ("[+]" if downloaded else "[-]", counted(downloaded), os.path.abspath(output_dir), failed))
    return downloaded, failed, interrupted


def extract_metadata(folder, csv_path=None):
    """Run ExifTool over a folder. Prints the 'Author' and 'Creator' values, or writes everything to a CSV.

    Returns (whether it worked, a few words on what was extracted for the summary).
    """
    if not folder or not os.path.isdir(folder):
        log("[!] %s does not exist! Canceling metadata extraction." % folder)
        return False, ""
    path = exif.usable_exiftool(log)
    if not path:
        return False, ""

    log("[*] Now extracting metadata from %s" % os.path.abspath(folder))
    try:
        if csv_path:
            count = exif.extract_all_to_csv(path, folder, csv_path)
            if count == 0:
                log("[-] ExifTool found no metadata in %s." % folder)
                return True, "none found"
            log("[+] Metadata for %s was written to %s" % (counted(count), csv_path))
            return True, "all fields of %s, in %s" % (counted(count), csv_path)

        count, values = exif.extract_authors(path, folder)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        log("[!] Metadata extraction failed: %s" % error)
        return False, ""

    log("[*] ExifTool read %s." % counted(count))
    log()
    if not values:
        log("[-] No 'Author' or 'Creator' metadata was found.")
        return True, "no 'Author' or 'Creator' values"
    log("[+] Extracted 'Author' and 'Creator' metadata (%s)" % counted(len(values), "value"))
    log()
    for value in values:
        log("    " + value)
    return True, "%d 'Author' and 'Creator' %s" % (len(values), "value" if len(values) == 1 else "values")


def read_link_list(path):
    """The links in a list file, one per line. Windows PowerShell writes such files as UTF-16."""
    with open(path, "rb") as source:
        raw = source.read()
    encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    lines = raw.decode(encoding, errors="replace").splitlines()
    return [links.plain_ascii(line.strip()) for line in lines if line.strip()]


def _cannot_use_folder(path):
    """Why files cannot be put in this folder (which may still have to be made), or None."""
    path = os.path.abspath(path)
    if os.path.exists(path) and not os.path.isdir(path):
        return "it is not a folder"
    # The nearest folder that exists is the one that decides
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return "it cannot be made"
        path = parent
    if not os.path.isdir(path):
        return "%s is not a folder" % path
    if not os.access(path, os.W_OK):
        return "%s is not writable" % path
    return None


def _cannot_write(path):
    """Why a file cannot be written at this path, or None if nothing stands in the way."""
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        return "the folder %s does not exist" % folder
    if os.path.isdir(path):
        return "it is a folder"
    if not os.access(path if os.path.exists(path) else folder, os.W_OK):
        return "it is not writable"
    return None


def write_link_list(path, file_links):
    """Write the links to a file. Returns None, or why the file could not be written."""
    try:
        with open(path, "w", encoding="ascii", errors="replace", newline="\n") as target:
            target.write("\n".join(file_links) + "\n")
    except OSError as error:
        return str(error)
    return None


def store_links(list_out, file_links, interrupted, summary):
    """What a search does with the links it found, so that they outlast the run: they are written to
    the file that was asked for with --list-out. If that fails they are printed instead, and so they
    are after an interrupted search without such a file, which ends the run. Adds the row for the link
    list to summary. Returns False if a list that was asked for could not be written."""
    if list_out and file_links:
        problem = write_link_list(list_out, file_links)
        if problem is not None:
            # The row comes first: printing thousands of links takes a while, and Ctrl+C may end it
            summary.append(("Link list", "could not be written"))
            log("[!] The links could not be written to %s (%s). They are listed here instead:" % (list_out, problem))
            for link in file_links:
                log("    " + link)
            return False
        log("[+] The links were written to %s" % list_out)
        summary.append(("Link list", list_out))
        if interrupted:
            log("[*] To download these files later, run again with: --list-in \"%s\"" % list_out)
    elif interrupted and file_links:
        log("[*] The links found so far (use --list-out FILE to have them written to a file):")
        for link in file_links:
            log("    " + link)
    return True


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m justosint",
        description="%s finds the documents a domain has published, downloads them, and reads their metadata with ExifTool." % NAME,
    )
    parser.add_argument("-d", "--domain", help="the target domain to search for files")
    parser.add_argument(
        "-t", "--types", default=", ".join(links.DEFAULT_FILE_TYPES),
        help='comma separated file extensions to search for (default: "%(default)s")',
    )
    parser.add_argument("-o", "--output-dir", default="", help="directory to store downloaded files in (default: named after the date and time)")
    parser.add_argument("--list-out", metavar="FILE", default="", help="write the links that were found to FILE")
    parser.add_argument("--list-in", metavar="FILE", default="", help="skip searching and use the links in FILE (one per line)")
    parser.add_argument("--download", action="store_true", help="download the files without asking")
    parser.add_argument("--extract", action="store_true", help="extract metadata without asking")
    parser.add_argument("--csv", metavar="FILE", default="", help="when extracting, write all metadata (not just Author and Creator) to FILE")
    parser.add_argument(
        "--user-agent", default="",
        help="the User-Agent to send with plain web requests (default: a browser-like one for the search engines "
        'and the target site, "%s" for the Internet Archive)' % PROGRAM_USER_AGENT,
    )
    parser.add_argument("--max-pages", type=int, default=5, help="the most result pages to read per search (default: %(default)s)")
    parser.add_argument("--delay", type=float, default=2, help="seconds to wait between two requests to the same search engine (default: %(default)s)")
    parser.add_argument(
        "--sources", default=",".join(SOURCES),
        help="where to look with plain web requests: archive (the Internet Archive), bing, duckduckgo (default: %(default)s)",
    )
    parser.add_argument(
        "--browser", nargs="?", const=",".join(BROWSER_ENGINES), default="", metavar="ENGINES",
        help="also search in a visible browser window that you can use to answer any check the search engine "
        "shows: google, bing, duckduckgo (all three if no list is given). Needs 'pip install selenium'.",
    )
    parser.add_argument("--browser-name", choices=("chrome", "edge"), default="chrome", help="the browser to open (default: %(default)s)")
    parser.add_argument(
        "--google-lookup", choices=("click", "request", "off"), default="click",
        help="Google's results do not show their address. How to read it: click = click each result in the "
        "browser window (default); request = ask Google's link with a plain web request, without the browser "
        "(Google's robots.txt asks programs not to do that); off = leave such results out",
    )
    parser.add_argument("--extract-dir", metavar="DIR", default="", help="only extract metadata from the files in DIR")
    parser.add_argument("--install-exiftool", action="store_true", help="Windows: download the current ExifTool and put it next to %s" % NAME)
    parser.add_argument("--version", action="version", version="%s %s" % (NAME, __version__))
    return parser


def main(argv=None):
    """Run the command line. Returns the exit code: 0, 1 if something asked for could not be done,
    130 after Ctrl+C."""
    # Metadata can hold characters the console cannot show (a Windows console often knows one code page
    # only). Show a "?" for those instead of stopping with an error.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        return _run(argv)
    except KeyboardInterrupt:
        # Ctrl+C before there is anything to sum up, in the instant between the end of the search and
        # what follows it, or once more while the summary is being printed
        log()
        log("[!] Interrupted.")
        return 130


def _run(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    log(BANNER)

    if args.install_exiftool:
        return 0 if exif.install_exiftool(log=log) else 1

    if args.extract_dir:
        return 0 if extract_metadata(args.extract_dir, args.csv or None)[0] else 1

    if not args.domain and not args.list_in:
        parser.error("the target domain is required: -d targetdomain.com (or a list of links: --list-in FILE)")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if not 0 <= args.delay <= 3600:  # also turns away "nan" and "inf"
        parser.error("--delay must be between 0 and 3600 seconds")
    file_types = links.parse_file_types(args.types)
    for entry in args.types.split(","):
        if entry.strip() and entry.strip().lstrip(".").lower() not in file_types:
            log("[!] '%s' is not a file type and is left out. File types are given as a comma separated list such as \"pdf, docx\"." % entry.strip())
    if not file_types:
        log('[!] No file types to search for. Pass a comma separated list such as "pdf, docx".')
        return 1
    # A search can take a long time: find out now, not afterwards, that its result cannot be stored
    if args.output_dir:
        problem = _cannot_use_folder(args.output_dir)
        if problem:
            log("[!] The output directory %s cannot be used: %s." % (args.output_dir, problem))
            return 1

    archive_copies = {}
    tallies = []
    listed = True  # False while the links that were found exist nowhere but in this run
    searched = interrupted = False
    summary = []

    def finish(status):
        # Every run that searched, or read a list of links, ends with the summary
        log()
        log_summary(subject, summary, files_by_type(file_links, file_types), tallies, len(file_links))
        return status

    if args.list_in:
        # If a list of links was given, use that instead of searching
        if not os.path.isfile(args.list_in):
            log("[!] The file list %s does not exist." % args.list_in)
            return 1
        try:
            file_links = read_link_list(args.list_in)
        except OSError as error:
            log("[!] The file list %s could not be read: %s" % (args.list_in, error))
            return 1
        subject = args.list_in
        log_settings([("Link list", args.list_in), ("File types", ", ".join(file_types))])
        log()
        log("%s A total of %s found in the list." % ("[+]" if file_links else "[-]", counted(len(file_links), verb=True)))
        summary.append(("Links in the list", len(file_links)))
    else:
        domain = links.host_from_target(args.domain)
        if not links.is_valid_host(domain):
            log("[!] '%s' is not a domain name. Use a name such as targetdomain.com." % args.domain)
            return 1
        http_sources = _name_list(args.sources, SOURCES, "--sources", parser)
        browser_engines = _name_list(args.browser, BROWSER_ENGINES, "--browser", parser)
        if args.list_out:
            problem = _cannot_write(args.list_out)
            if problem:
                log("[!] The links cannot be written to %s: %s." % (args.list_out, problem))
                return 1

        subject = domain
        plain = [SOURCE_NAMES[name] for name in http_sources if name == "archive" or name not in browser_engines]
        settings = [("Target domain", domain), ("File types", ", ".join(file_types)), ("Sources", ", ".join(plain) or "none")]
        if browser_engines:
            settings.append(("Browser", "%s (%s)" % (", ".join(SOURCE_NAMES[name] for name in browser_engines), args.browser_name)))
        log_settings(settings)
        log()

        file_links, archive_copies, interrupted, tallies = find_files(
            domain, file_types, http_sources, browser_engines, args.user_agent or None, args.max_pages, args.delay,
            args.browser_name, args.google_lookup,
        )
        log()
        log("%s A total of %s discovered on %s." % ("[+]" if file_links else "[-]", counted(len(file_links), verb=True), domain))
        log()
        summary.append(("Files found", "%d (the search was interrupted)" % len(file_links) if interrupted else len(file_links)))
        searched = True
        # With --list-out the links are in the file, or were printed when it could not be written
        listed = bool(args.list_out)

    # What follows the search can be cut short with Ctrl+C as well. The run still ends with the summary.
    list_written = True
    try:
        if searched:
            list_written = store_links(args.list_out, file_links, interrupted, summary)
        # An interrupted search ends the run: nothing is downloaded
        status = 130 if interrupted else _download_and_extract(args, file_links, archive_copies, file_types, listed, summary)
    except KeyboardInterrupt:
        log()
        log("[!] Interrupted.")
        status = 130
    # A link list that could not be written is something that was asked for and could not be done
    return finish(status or (0 if list_written else 1))


def _download_and_extract(args, file_links, archive_copies, file_types, listed, summary):
    """What a run does with the links it has: download the files, then read their metadata, each after
    asking unless the options say so. Adds a row to summary for each step that was taken and returns
    the exit code. listed is False if the links exist nowhere but in this run."""
    if not file_links:
        log("[-] Nothing to download.")
        return 0

    # If no output directory was named, use one named after the current date and time
    output_dir = args.output_dir or datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")

    if args.download or ask_yes_no("Would you like to download all of the files discovered?"):
        try:
            downloaded, failed, interrupted = download_files(file_links, output_dir, args.user_agent or None, archive_copies, file_types)
        except OSError as error:
            log("[!] The output directory %s cannot be used: %s" % (output_dir, error))
            if not listed:
                log("[*] The links that were found (use --list-out FILE to have them written to a file):")
                for link in file_links:
                    log("    " + link)
            summary.append(("Files downloaded", "0 (the output directory could not be used)"))
            return 1
        if interrupted:
            value = "%d of %d (%sinterrupted)" % (downloaded, len(file_links), "%d failed, " % failed if failed else "")
        else:
            value = "%d (%d failed)" % (downloaded, failed) if failed else downloaded
        summary.append(("Files downloaded", value))
        if downloaded:
            summary.append(("Saved to", os.path.abspath(output_dir)))
        if interrupted:
            return 130
        if downloaded == 0:
            # Every download failed: there is nothing to extract metadata from
            return 1 if failed else 0
    else:
        log("[*] No files will be downloaded.")

    # Without a folder of files there is nothing to extract metadata from
    if not os.path.isdir(output_dir):
        return 0

    if args.extract or ask_yes_no("Would you like to extract metadata from all of the files downloaded now?"):
        try:
            worked, extracted = extract_metadata(output_dir, args.csv or None)
        except KeyboardInterrupt:
            summary.append(("Metadata", "interrupted"))
            raise
        if not worked:
            summary.append(("Metadata", "could not be extracted"))
            return 1
        if extracted:
            summary.append(("Metadata", extracted))
    else:
        log(
            "[*] No metadata will be extracted at this time. To extract it later run: "
            'python -m justosint --extract-dir "%s"' % output_dir
        )
    return 0
