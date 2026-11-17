import base64
import unittest

from powermeta import links

from .support import duckduckgo_page, fixture


def wrap(address, marker="a1"):
    encoded = base64.urlsafe_b64encode(address.encode()).decode().rstrip("=")
    return "https://www.bing.com/ck/a?!&&p=abc123&ptn=3&ver=2&hsh=4&fclid=0dd4&u=" + marker + encoded + "&ntb=1"


class InputHandling(unittest.TestCase):
    def test_file_types_are_split_and_cleaned(self):
        self.assertEqual(links.parse_file_types("pdf, .DOCX,xlsx,,pdf"), ["pdf", "docx", "xlsx"])
        self.assertEqual(links.parse_file_types(["PDF", "doc"]), ["pdf", "doc"])
        self.assertEqual(links.parse_file_types("p df, pdf;calc, " + "a" * 13), [])

    def test_host_is_taken_from_what_the_user_typed(self):
        cases = {
            "targetdomain.com": "targetdomain.com",
            " HTTPS://www.TargetDomain.com/path?q=1 ": "www.targetdomain.com",
            "*.targetdomain.com": "targetdomain.com",
            "targetdomain.com.": "targetdomain.com",
            "user@targetdomain.com:8443": "targetdomain.com",
            "http://targetdomain.com:8080/": "targetdomain.com",
        }
        for typed, expected in cases.items():
            self.assertEqual(links.host_from_target(typed), expected, typed)

    def test_host_validation(self):
        for host in ("targetdomain.com", "a", "café.example", "127.0.0.1"):
            self.assertTrue(links.is_valid_host(host), host)
        for host in ("", "bad domain", "-x.com", "x.com-", "a;b.com", "a_b.com"):
            self.assertFalse(links.is_valid_host(host), host)


class BingLinks(unittest.TestCase):
    def test_wrapped_links_decode(self):
        for address in (
            "https://example.com/a/report.pdf",
            "https://example.com/a/b?x=1&y=%20z.docx",
            "http://sub.example.com/café/menü.pdf",
            "https://example.com/aa",
            "https://example.com/aaa",
            "https://example.com/aaaa",
        ):
            self.assertEqual(links.unwrap_bing(wrap(address)), address)

    def test_other_marker_and_plain_links(self):
        self.assertEqual(links.unwrap_bing(wrap("https://example.com/x.pdf", "a0")), "https://example.com/x.pdf")
        self.assertEqual(links.unwrap_bing("https://example.com/x.pdf"), "https://example.com/x.pdf")

    def test_undecodable_wrappers_give_nothing(self):
        self.assertIsNone(links.unwrap_bing("https://www.bing.com/ck/a?!&&p=abc"))
        self.assertIsNone(links.unwrap_bing("https://www.bing.com/ck/a?u=a1A"))
        self.assertIsNone(links.unwrap_bing(wrap("/images/search?q=x")))

    def test_page_saved_from_the_live_site(self):
        page = fixture("bing_results.html")
        self.assertEqual(links.count_bing_results(page), 10)
        found = [links.unwrap_bing(href) for href in links.links_from_html(page)]
        wanted = sorted({link for link in found if link and links.is_wanted_file(link, "legislature.mi.gov", "pdf")})
        self.assertEqual(
            wanted,
            [
                "https://www.legislature.mi.gov/documents/mcl/pdf/mcl-257-636-amended.pdf",
                "https://www.legislature.mi.gov/documents/mcl/pdf/mcl-257-636.pdf",
                "https://www.legislature.mi.gov/documents/mcl/pdf/mcl-chap257.pdf",
            ],
        )

    def test_unrelated_answer_saved_from_the_live_site(self):
        page = fixture("bing_unrelated.html")
        found = [links.unwrap_bing(href) for href in links.links_from_html(page)]
        self.assertGreater(links.count_bing_results(page), 0)
        self.assertEqual([link for link in found if link and links.is_on_domain(link, "nasa.gov")], [])


class DuckDuckGoLinks(unittest.TestCase):
    def test_wrapped_and_plain_links(self):
        wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Ffiles.example.com%2Fa%2Fbudget%2D2025.pdf%3Fdl%3D1&rut=0123abcd"
        self.assertEqual(links.unwrap_duckduckgo(wrapped), "https://files.example.com/a/budget-2025.pdf?dl=1")
        self.assertEqual(links.unwrap_duckduckgo("https://www.example.com/a.pdf"), "https://www.example.com/a.pdf")
        self.assertIsNone(links.unwrap_duckduckgo("//duckduckgo.com/l/?rut=abc"))
        self.assertIsNone(links.unwrap_duckduckgo("//duckduckgo.com/l/?uddg=javascript%3Aalert(1)"))

    def test_bot_check_saved_from_the_live_site(self):
        page = fixture("duckduckgo_bot_check.html")
        self.assertTrue(links.is_duckduckgo_bot_check(page))
        self.assertIsNone(links.duckduckgo_next_form(page))

    def test_results_page_is_not_a_bot_check(self):
        page = duckduckgo_page(["https://www.example.com/a.pdf"], next_offset=30)
        self.assertFalse(links.is_duckduckgo_bot_check(page))
        self.assertEqual(links.count_duckduckgo_results(page), 1)

    def test_next_form(self):
        page = duckduckgo_page([], next_offset=60, previous_offset=0, query="site:example.com filetype:pdf")
        form = links.duckduckgo_next_form(page, after=30)
        self.assertEqual(form, {"q": "site:example.com filetype:pdf", "s": "60", "vqd": "4-222&x"})
        self.assertIsNone(links.duckduckgo_next_form(page, after=60))

    def test_next_form_is_the_nearest_one(self):
        # Another form on the page with a query and a larger offset is not "Next"
        decoy = '<form action="/elsewhere"><input type="hidden" name="q" value="x"><input type="hidden" name="s" value="999999"></form>'
        page = duckduckgo_page([], next_offset=30).replace("</body>", decoy + "</body>")
        self.assertEqual(links.duckduckgo_next_form(page)["s"], "30")
        self.assertEqual(links.duckduckgo_next_form(page, after=30)["s"], "999999")
        self.assertIsNone(links.duckduckgo_next_form(page.replace('value="30"', 'value="3 0"').replace('value="999999"', 'value="-5"')))


class GoogleLinks(unittest.TestCase):
    def test_kinds_of_link(self):
        self.assertEqual(links.classify_google_link("https://www.example.com/a.pdf"), ("address", "https://www.example.com/a.pdf"))
        self.assertEqual(
            links.classify_google_link("https://www.google.com/url?q=https://www.example.com/a.pdf&sa=U&ved=1"),
            ("address", "https://www.example.com/a.pdf"),
        )
        self.assertEqual(
            links.classify_google_link("https://www.google.co.uk/url?url=https%3A%2F%2Fexample.com%2Fb.docx&x=1"),
            ("address", "https://example.com/b.docx"),
        )
        hidden = "https://www.google.com/goto?url=CAESabc123"
        self.assertEqual(links.classify_google_link(hidden), ("redirect", hidden))
        for own in ("https://www.google.com/search?q=x&start=10", "https://support.google.com/websearch", "https://accounts.google.com/x", "javascript:void(0)", "/search?q=x"):
            self.assertEqual(links.classify_google_link(own), ("other", None), own)


class FileLinks(unittest.TestCase):
    def test_which_links_are_wanted(self):
        cases = [
            ("https://a.example.com/x/report.pdf", "pdf", True),
            ("https://example.com/x/report.PDF?dl=1", "pdf", True),
            ("https://example.com/get.php?f=a.pdf&t=1", "pdf", True),
            ("https://EXAMPLE.com/A.PDF", "pdf", True),
            ("https://example.com/a.docx", "doc", False),
            ("https://example.com/a.doc", "doc", True),
            ("https://example.com/pdf/list", "pdf", False),
            ("https://notexample.com/a.pdf", "pdf", False),
            ("https://example.com.evil.org/a.pdf", "pdf", False),
            ("ftp://example.com/a.pdf", "pdf", False),
            ("/search?q=site%3aexample.com+filetype%3apdf", "pdf", False),
            ("http://example.com:99999/a.pdf", "pdf", False),
        ]
        for link, file_type, expected in cases:
            self.assertEqual(links.is_wanted_file(link, "example.com", file_type), expected, link)

    def test_wanted_type(self):
        self.assertEqual(links.wanted_type("https://example.com/a.docx", "example.com", ["pdf", "doc", "docx"]), "docx")
        self.assertIsNone(links.wanted_type("https://example.com/a.html", "example.com", ["pdf"]))

    def test_link_key_ignores_the_scheme(self):
        self.assertEqual(
            links.link_key("http://WWW.Example.com:80/docs/Report.pdf?v=1"),
            links.link_key("https://www.example.com/docs/Report.pdf?v=1"),
        )
        self.assertEqual(links.link_key("https://www.example.com:8443/a.pdf"), "www.example.com:8443/a.pdf")
        self.assertNotEqual(links.link_key("https://example.com/A.pdf"), links.link_key("https://example.com/a.pdf"))

    def test_plain_ascii(self):
        self.assertEqual(links.plain_ascii("https://example.com/café menu.pdf"), "https://example.com/caf%C3%A9%20menu.pdf")
        self.assertEqual(links.plain_ascii("https://example.com/a%20b.pdf?x=1&y=2"), "https://example.com/a%20b.pdf?x=1&y=2")


class FileNames(unittest.TestCase):
    def test_names(self):
        cases = {
            "https://example.com/a/report.pdf": "report.pdf",
            "https://example.com/a/My%20Report%20(final).pdf?x=1": "My%20Report%20(final).pdf",
            "https://example.com/a/": "download",
            "https://example.com": "download",
            "https://example.com/x/$(calc);`rm`.pdf": "_(calc)__rm_.pdf",
            "https://example.com/x/a[1].pdf": "a_1_.pdf",
            "https://example.com/con.pdf": "_con.pdf",
            "https://example.com/x/café.pdf": "caf_.pdf",
            "https://example.com/x/a.pdf.": "a.pdf",
            "https://example.com/a.pdf/exiftool.exe": "_exiftool.exe.pdf",
            "https://example.com/x/EXIFTOOL": "_EXIFTOOL",
            "https://example.com/x/.ExifTool_config": "_.ExifTool_config.download",
            "https://example.com/x/-o.pdf": "_-o.pdf",
            "https://example.com/x/my-exiftool-notes.pdf": "my-exiftool-notes.pdf",
            "https://example.com/x/..%2F..%2Fetc%2Fpasswd.pdf": "_..%2F..%2Fetc%2Fpasswd.pdf",
        }
        for link, expected in cases.items():
            self.assertEqual(links.safe_file_name(link), expected, link)

    def test_long_name_keeps_its_extension(self):
        name = links.safe_file_name("https://example.com/" + "a" * 300 + ".pdf")
        self.assertEqual(len(name), 150)
        self.assertTrue(name.endswith(".pdf"))
        # A name that had to get the wanted ending added still has it after shortening
        name = links.safe_file_name("https://example.com/" + "a" * 300 + ".exe?x=.pdf")
        self.assertEqual(len(name), 150)
        self.assertTrue(name.endswith("a.pdf"))
        name = links.safe_file_name("https://example.com/" + "a" * 300 + "." + "b" * 30)
        self.assertEqual(len(name), 150)
        self.assertTrue(name.endswith(".download"))

    def test_the_site_does_not_choose_the_ending(self):
        # A link counts as a wanted file when its query names the type, so the last part of the path
        # can be anything. Saved under that name, a file could be run by mistake or picked up by a
        # program that is started in the folder.
        cases = {
            "https://example.com/files/argparse.py?v=.pdf": "argparse.py.pdf",
            "https://example.com/files/setup.exe?doc=.pdf": "setup.exe.pdf",
            "https://example.com/files/run.bat?x.pdf": "run.bat.pdf",
            "https://example.com/files/report.pdf.lnk": "report.pdf.lnk.pdf",
            "https://example.com/files/a.pdf.part": "a.pdf.part.pdf",
            "https://example.com/get.php?file=annual.docx&x=1": "get.php.docx",
            "https://example.com/download?file=annual.docx": "download.docx",
            "https://example.com/?file=annual.xlsx": "download.xlsx",
            "https://example.com/files/REPORT.PDF": "REPORT.PDF",
            "https://example.com/files/setup.exe.": "setup.exe.download",
            "https://example.com/files/tool.py": "tool.py.download",
            "https://example.com/files/readme": "readme",
        }
        for link, expected in cases.items():
            self.assertEqual(links.safe_file_name(link), expected, link)
        # The wanted types are those of this run
        self.assertEqual(links.safe_file_name("https://example.com/a.odt", ["odt"]), "a.odt")
        self.assertEqual(links.safe_file_name("https://example.com/a.pdf", ["odt"]), "a.pdf.download")
        self.assertEqual(links.safe_file_name("https://example.com/a.pdf?as=b.odt", ["odt"]), "a.pdf.odt")

    def test_device_names_with_spaces(self):
        for link, expected in {
            "https://example.com/dir/nul .pdf": "_nul .pdf",
            "https://example.com/dir/prn  .x.pdf": "_prn  .x.pdf",
            "https://example.com/dir/COM0.pdf": "_COM0.pdf",
            "https://example.com/dir/console.pdf": "console.pdf",
        }.items():
            self.assertEqual(links.safe_file_name(link), expected, link)


class Pages(unittest.TestCase):
    def test_the_href_is_read_from_the_right_attribute(self):
        cases = {
            '<a data-href="http://t.com/decoy.pdf" href="http://t.com/real.pdf">x</a>': ["http://t.com/real.pdf"],
            '<a title="x href=http://t.com/decoy.pdf" href="http://t.com/real.pdf">x</a>': ["http://t.com/real.pdf"],
            '<a title="a>b" href="http://t.com/gt.pdf">x</a>': ["http://t.com/gt.pdf"],
            "<A CLASS=x HREF='http://t.com/a.pdf?x=1&amp;y=2'>x</A>": ["http://t.com/a.pdf?x=1&y=2"],
            "<a href=http://t.com/bare.pdf>x</a><a>no address</a><a href=''>empty</a>": ["http://t.com/bare.pdf"],
            '<script>var s = "<a href=\\"http://t.com/in-script.pdf\\">";</script><a href="/real">x</a>': ["/real"],
            "": [],
        }
        for page, expected in cases.items():
            self.assertEqual(links.links_from_html(page), expected, page)
        self.assertEqual(links.links_from_html(None), [])

    def test_a_page_with_thousands_of_unfinished_tags_is_read_quickly(self):
        import time

        started = time.time()
        self.assertEqual(links.links_from_html("<a " * 20000), [])
        self.assertIsNone(links.duckduckgo_next_form("<form>" * 20000))
        self.assertLess(time.time() - started, 5)

    def test_the_older_reader_is_used_when_the_parser_gives_up(self):
        from .support import patched

        def refuse(page):
            return None

        page = duckduckgo_page(["https://example.com/a.pdf?x=1&amp;y=2"], next_offset=30)
        with patched(links, _read_page=refuse):
            self.assertEqual(links.links_from_html(page), ["https://example.com/a.pdf?x=1&y=2"])
            self.assertEqual(links.duckduckgo_next_form(page), {"q": "q", "s": "30", "vqd": "4-222&x"})


class ArchiveIndex(unittest.TestCase):
    def test_filter_skips_the_host_name(self):
        import re

        field, pattern = links.archive_filter(["pdf", "docx", "doc"]).split(":", 1)
        self.assertEqual(field, "original")
        java_like = pattern.replace("(?i)", "")
        cases = {
            "http://www.example.com:80/docs/Report.pdf": True,
            "https://example.com/a/B.PDF": True,
            "https://example.com/get.php?f=a.pdf&t=1": True,
            "https://example.com/a.docx": True,
            "https://example.com/a.docxx": False,
            "https://example.com/pdf/list.html": False,
            "http://www.doc.gov/index.html": False,
            "http://www.doc.gov:80/files/Plan.doc": True,
            "https://pdf.example.com/": False,
        }
        for address, expected in cases.items():
            # The index matches the expression against the whole address
            self.assertEqual(re.fullmatch(java_like, address, re.I) is not None, expected, address)

    def test_answer_is_read(self):
        answer = (
            "http://www.example.com:80/docs/Report.pdf 20190102030405\n"
            "https://files.example.com/a/budget.xlsx?v=2 20210607080910\r\n"
            "https://www.example.com/about.html 20200101000000\n"
            "https://www.example.com/docs/Report.pdf 20220102030405\n"
            "https://other.org/x.pdf 20200101000000\n"
            "not an index line\n"
            "https://example.com/short.pdf 2020\n"
        )
        entries = links.parse_archive_index(answer, "example.com", ["pdf", "xlsx"])
        self.assertEqual(
            [entry.link for entry in entries],
            ["http://www.example.com/docs/Report.pdf", "https://files.example.com/a/budget.xlsx?v=2", "https://www.example.com/docs/Report.pdf"],
        )
        self.assertEqual(
            entries[0].archive_copy, "https://web.archive.org/web/20190102030405id_/http://www.example.com:80/docs/Report.pdf"
        )
        self.assertEqual(links.parse_archive_index("", "example.com", ["pdf"]), [])

    def test_addresses_in_the_index_are_made_plain(self):
        # An address in the index can hold characters that would change what the terminal shows, or
        # that cannot be sent in a request
        answer = "http://example.com/files/B\u00fccher.pdf 20200101000000\nhttp://example.com/files/\x1b[31mred.pdf 20200101000000\n"
        entries = links.parse_archive_index(answer, "example.com", ["pdf"])
        self.assertEqual(
            [entry.link for entry in entries],
            ["http://example.com/files/B%C3%BCcher.pdf", "http://example.com/files/%1B[31mred.pdf"],
        )
        self.assertEqual(entries[0].archive_copy, "https://web.archive.org/web/20200101000000id_/http://example.com/files/B%C3%BCcher.pdf")


if __name__ == "__main__":
    unittest.main()
