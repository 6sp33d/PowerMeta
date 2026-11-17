"""The command line: search, download, extract."""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
import time

from . import __version__, exif, links, sources
from .web import DEFAULT_USER_AGENT, FetchError, Fetcher, download

SOURCES = ("archive", "bing", "duckduckgo")
BROWSER_ENGINES = ("google", "bing", "duckduckgo")


def log(message=""):
    print(message, flush=True)


def ask_yes_no(question):
    """Ask a yes/no question. Without a terminal nothing can be asked, and the answer is no."""
    if sys.stdin is None or not sys.stdin.isatty():
        log("[*] %s Not asked, because there is no terminal: answered no." % question)
        return False
    while True:
        try:
            answer = input(question + " [Y/n] ").strip().lower()
        except EOFError:
            return False
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


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


def find_files(domain, file_types, http_sources, browser_engines, user_agent, max_pages, delay, browser_name):
    """Ask every chosen source. Returns (sorted links, {link: address of the Internet Archive's copy})."""
    found, by_key, archive_copies = [], {}, {}

    def keep(link):
        # Links are compared without "http://" or "https://": the Internet Archive often knows a file
        # under its old http address while a search engine gives today's https one
        key = links.link_key(link)
        if key not in by_key:
            by_key[key] = link
            found.append(link)
        return by_key[key]

    # The Internet Archive answers for all file types in a single request
    archive_entries = []
    if "archive" in http_sources:
        archive_entries = sources.search_archive(Fetcher(user_agent), domain, file_types, log=log)

    # An engine that is searched through the browser is not also asked with plain requests
    engines = [name for name in ("bing", "duckduckgo") if name in http_sources and name not in browser_engines]
    duckduckgo_blocked = False
    first = True
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
            for link in sources.search_bing(Fetcher(user_agent), domain, file_type, max_pages, delay, log):
                keep(link)
        if ask_duckduckgo:
            # DuckDuckGo is left alone for the rest of the run once it has refused to answer
            result, duckduckgo_blocked = sources.search_duckduckgo(
                Fetcher(user_agent), domain, file_type, max_pages, delay, log
            )
            for link in result:
                keep(link)

    if browser_engines:
        from . import browser

        for link in browser.search(browser_engines, domain, file_types, max_pages, delay, browser_name, log):
            keep(link)

    # The Archive's files are added last, so that for a file both know the search engine's current
    # address is the one kept. Either way the Archive's copy is remembered for it.
    for entry in archive_entries:
        archive_copies.setdefault(keep(entry.link), entry.archive_copy)

    return sorted(found), archive_copies


def download_files(file_links, output_dir, user_agent=DEFAULT_USER_AGENT, archive_copies=None):
    """Download every link into output_dir. Returns (downloaded, failed)."""
    file_links = [link.strip() for link in file_links if link and link.strip()]
    log("[*] Downloading %d files now." % len(file_links))
    if not file_links:
        return 0, 0

    if not os.path.isdir(output_dir):
        log("[*] The output directory %s does not exist. Creating directory %s." % (output_dir, output_dir))
        os.makedirs(output_dir, exist_ok=True)

    downloaded = failed = 0
    for link in file_links:
        # When downloading files if a file has the same name a number is added to it
        name = links.safe_file_name(link)
        candidate, number = name, 1
        while os.path.exists(os.path.join(output_dir, candidate)):
            base, extension = os.path.splitext(name)
            candidate = "%s-%d%s" % (base, number, extension)
            number += 1
        destination = os.path.join(output_dir, candidate)

        log("Now Downloading %s" % link)
        problem = None
        try:
            # Target servers often have expired or self-signed certificates, so certificate errors are
            # ignored for them - and only for them
            download(link, destination, user_agent, verify_certificate=False)
        except FetchError as error:
            problem = str(error)

        # A file that is gone from the site may still be in the Internet Archive
        if problem and archive_copies and link in archive_copies:
            log("[*] Not available from the site (%s). Trying the copy in the Internet Archive." % problem)
            time.sleep(1)
            try:
                download(archive_copies[link], destination, user_agent, verify_certificate=True)
                problem = None
            except FetchError as error:
                problem = str(error)

        if problem:
            log("[!] Could not download %s : %s" % (link, problem))
            failed += 1
        else:
            downloaded += 1

    log("[*] Downloaded %d files to %s (%d failed)." % (downloaded, os.path.abspath(output_dir), failed))
    return downloaded, failed


def extract_metadata(folder, csv_path=None):
    """Run ExifTool over a folder. Prints the 'Author' and 'Creator' values, or writes everything to a CSV."""
    if not folder or not os.path.isdir(folder):
        log("[*] %s does not exist! Canceling metadata extraction." % folder)
        return False
    path = exif.usable_exiftool(log)
    if not path:
        return False

    log("[*] Now extracting metadata from %s" % folder)
    try:
        if csv_path:
            count = exif.extract_all_to_csv(path, folder, csv_path)
            if count == 0:
                log("[*] ExifTool found no metadata in %s." % folder)
            else:
                log("[*] Metadata for %d files was written to %s" % (count, csv_path))
            return True

        count, values = exif.extract_authors(path, folder)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        log("[!] Metadata extraction failed: %s" % error)
        return False

    log("[*] ExifTool read %d files." % count)
    log("\n[*] Extracted 'Author' and 'Creator' metadata")
    if not values:
        log("[*] No 'Author' or 'Creator' metadata was found.")
    for value in values:
        log(value)
    log()
    return True


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m powermeta",
        description="Find the documents a domain has published, download them, and read their metadata with ExifTool.",
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
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="the User-Agent to send")
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
    parser.add_argument("--extract-dir", metavar="DIR", default="", help="only extract metadata from the files in DIR")
    parser.add_argument("--install-exiftool", action="store_true", help="Windows: download the current ExifTool and put it next to PowerMeta")
    parser.add_argument("--version", action="version", version="PowerMeta (Python) " + __version__)
    return parser


def main(argv=None):
    # Metadata can hold characters the console cannot show (a Windows console often knows one code page
    # only). Show a "?" for those instead of stopping with an error.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.install_exiftool:
        return 0 if exif.install_exiftool(log=log) else 1

    if args.extract_dir:
        return 0 if extract_metadata(args.extract_dir, args.csv or None) else 1

    if not args.domain:
        parser.error("the target domain is required: -d targetdomain.com")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")

    archive_copies = {}
    if args.list_in:
        # If a list of links was given, use that instead of searching
        if not os.path.isfile(args.list_in):
            log("[!] The file list %s does not exist." % args.list_in)
            return 1
        with open(args.list_in, encoding="utf-8-sig", errors="replace") as source:
            file_links = [line.strip() for line in source if line.strip()]
        log("[*] Using the file list at %s" % args.list_in)
        log("[*] A total of %d files were found." % len(file_links))
    else:
        domain = links.host_from_target(args.domain)
        if not links.is_valid_host(domain):
            log("[!] '%s' is not a domain name. Use a name such as targetdomain.com." % args.domain)
            return 1
        file_types = links.parse_file_types(args.types)
        if not file_types:
            log('[!] No file types to search for. Pass a comma separated list such as "pdf, docx".')
            return 1
        http_sources = _name_list(args.sources, SOURCES, "--sources", parser)
        browser_engines = _name_list(args.browser, BROWSER_ENGINES, "--browser", parser)

        file_links, archive_copies = find_files(
            domain, file_types, http_sources, browser_engines, args.user_agent, args.max_pages, args.delay, args.browser_name
        )
        log("[*] A total of %d files were discovered." % len(file_links))
        log()

        if args.list_out and file_links:
            log("[*] Writing links to a file at %s" % args.list_out)
            with open(args.list_out, "w", encoding="ascii", errors="replace", newline="\n") as target:
                target.write("\n".join(file_links) + "\n")

    if not file_links:
        log("[*] Nothing to download.")
        return 0

    # If no output directory was named, use one named after the current date and time
    output_dir = args.output_dir or datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")

    if args.download or ask_yes_no("Would you like to download all of the files discovered?"):
        download_files(file_links, output_dir, args.user_agent, archive_copies)
    else:
        log("[*] No files will be downloaded.")

    # Without a folder of files there is nothing to extract metadata from
    if not os.path.isdir(output_dir):
        return 0

    if args.extract or ask_yes_no("Would you like to extract metadata from all of the files downloaded now?"):
        extract_metadata(output_dir, args.csv or None)
    else:
        log(
            "[*] No metadata will be extracted at this time. To extract it later run: "
            'python -m powermeta --extract-dir "%s"' % output_dir
        )
    return 0
