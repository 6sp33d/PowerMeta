# justOSINT

```
       _            __  ____  _____ _____   ________
      (_)_  _______/ /_/ __ \/ ___//  _/ | / /_  __/
     / / / / / ___/ __/ / / /\__ \ / //  |/ / / /
    / / /_/ (__  ) /_/ /_/ /___/ // // /|  / / /
 __/ /\__,_/____/\__/\____//____/___/_/ |_/ /_/
/___/

           It's not theft, it's justOSINT
```

justOSINT finds the documents a domain has published, downloads them, and reads their metadata. Documents on public websites often still carry the names of the people who wrote them and the software they were written with: usernames, software titles and versions, sometimes computer names. justOSINT collects them.

It looks for files in the Internet Archive and in search engines (with searches like `site:targetdomain.com filetype:pdf`), by default for `pdf, docx, xlsx, doc, xls, pptx, ppt`, and reads the metadata with [ExifTool](https://exiftool.org/).

There are two versions with the same job and the same style of output: a Python version (`python -m justosint`), which can also run the searches in a browser window, and a PowerShell version (`justOSINT.ps1`).

## What you need

- Python 3.7 or later (tested with 3.8 and 3.11 to 3.14), or Windows PowerShell 5.1 / PowerShell 7 for the PowerShell version
- ExifTool 12.24 or later for the metadata (see [ExifTool](#exiftool))
- For searching in a browser window: `pip install selenium`, and Chrome or Edge

## Quick start

Run these from the folder that holds this README.

``` PowerShell
python -m justosint -d targetdomain.com
python -m justosint -d targetdomain.com --browser
python -m justosint -d targetdomain.com -t "pdf, docx" --download --extract
python -m justosint -d targetdomain.com --download --extract --csv all-target-metadata.csv
```

Without `--download` and `--extract` justOSINT asks before it downloads the files and before it reads their metadata. `--browser` also searches Google, Bing and DuckDuckGo in a browser window that you watch (see [Searching in a browser window](#searching-in-a-browser-window)).

The start and the end of a run look like this:

```
       _            __  ____  _____ _____   ________
      (_)_  _______/ /_/ __ \/ ___//  _/ | / /_  __/
     / / / / / ___/ __/ / / /\__ \ / //  |/ / / /
    / / /_/ (__  ) /_/ /_/ /___/ // // /|  / / /
 __/ /\__,_/____/\__/\____//____/___/_/ |_/ /_/
/___/

           It's not theft, it's justOSINT

[*] Target domain : targetdomain.com
[*] File types    : pdf, docx, xlsx
[*] Sources       : Internet Archive
[*] Browser       : Google, Bing, DuckDuckGo (chrome)

[*] Asking the Internet Archive which files it has seen on targetdomain.com
...
[+] A total of 5 files were discovered on targetdomain.com.

[+] The links were written to targetdomain.com-links.txt
[*] Downloading 5 files to /home/you/targetdomain.com-files
...
[+] Downloaded 5 files to /home/you/targetdomain.com-files (0 failed).
[*] Now extracting metadata from /home/you/targetdomain.com-files
[*] ExifTool read 5 files.

[+] Extracted 'Author' and 'Creator' metadata (8 values)

    a.accountant
    Acrobat PDFMaker 9
    former.employee
    Jane Doe
    jsmith
    Microsoft Excel
    Microsoft Office Word
    Microsoft Word 2016

[*] Summary for targetdomain.com
    Files found      : 5
    Link list        : targetdomain.com-links.txt
    Files downloaded : 5
    Saved to         : /home/you/targetdomain.com-files
    Metadata         : 8 'Author' and 'Creator' values

    Files by type
        pdf   3
        docx  1
        xlsx  1

    Files by source
        Internet Archive      3
        Google (browser)      2
        Bing (browser)        0  (left out)
        DuckDuckGo (browser)  1
        (a file that several sources gave is counted for each of them)
```

Lines are marked `[*]` for what is being done, `[+]` for something found, `[-]` for nothing found, `[!]` for a problem and `[?]` for a question.

Every run that searches, or reads a list of links, ends with this summary, made to fit in one screenshot. That includes a run that finds nothing or downloads nothing and, in the Python version, a run that is stopped with Ctrl+C.

- The rows at the top say what was done. A step that was asked for and did not work is named as well, for example `Link list : could not be written` or `Metadata : could not be extracted`.
- **Files by type** lists every file type that was asked for, with the number of files found. A file counts as the type it is saved as. With `--list-in` (`-TargetFileList`), links that would not be saved as one of these types are counted in a last row, `other`.
- **Files by source** says how many different files each source gave. Two sources often know the same file, so these numbers can add up to more than the total; the summary says so when they do. A source that did not get through carries a remark:

| Remark | What it says about the source |
|---|---|
| `left out` | Nothing came of it. It was not used (in the Python version: Bing when it does not keep to the site), it refused at the start and was skipped, or the search was cut short before it gave a file. |
| `stopped early` | It gave some files and then refused, or the search was cut short. |
| `did not answer` | It was asked, and none of its searches got an answer. |
| `incomplete` | Its number may be too low: the Internet Archive holds more addresses than were read, a search engine left a request unanswered, or the address behind some Google results was not read. |

## Where the files are found

| Source | Used | Notes |
|---|---|---|
| Internet Archive | By default | Asks the Wayback Machine's index, a service made to be queried by programs, which addresses it has captured on the domain (up to 10,000). One request covers all file types. It lists files it has seen at any time, so some may be gone from the site; when such a file cannot be downloaded in the same run, justOSINT fetches the Archive's first copy of it instead. |
| Bing | By default | Bing hides each result behind a redirect link; justOSINT decodes these. Bing does not always keep to `site:`: when it does not know a site, and when it takes a search for an automated one, it answers with pages of other sites, without saying so. Nothing from another site is ever kept. The Python version first asks Bing for `site:targetdomain.com` alone and leaves Bing out for the run unless most results of that search are on the site; the PowerShell version tells you for each file type. |
| DuckDuckGo | By default | Uses DuckDuckGo's script-free results page. DuckDuckGo answers scripts from many networks with a bot check or an error instead of results. justOSINT tells you when that happens and leaves DuckDuckGo alone for the rest of the run. |
| Google | Python version, in a browser window | Google no longer answers plain web requests, and its result pages no longer contain the addresses of the results. The Python version runs the searches in a browser window that you watch (`--browser`). See [Google](#google). |
| Google Custom Search JSON API (`justOSINT-API.ps1`) | Existing customers only, until January 1, 2027 | Google has closed this API to new customers and discontinues it on January 1, 2027. See [Google API](#google-api). |

A search engine never lists everything a domain hosts, and what it returns to a script can differ from what it shows in a browser. If you already have a list of links (from a crawl, a sitemap, or a search you did by hand), pass it with `--list-in` (`-TargetFileList`) and justOSINT skips searching altogether.

## Searching in a browser window

Google does not answer scripts at all, and Bing and DuckDuckGo turn them away on many networks. With `--browser` the Python version opens a normal Chrome or Edge window and runs the searches there. When a search engine shows a check ("are you a robot"), a consent box or a button that has to be pressed first, justOSINT stops and asks you to deal with it in the window, then carries on.

``` PowerShell
pip install selenium
python -m justosint -d targetdomain.com --browser
python -m justosint -d targetdomain.com --browser google --browser-name edge
```

`--browser` alone uses Google, Bing and DuckDuckGo. The Internet Archive is still asked as well; `--sources` controls that.

What to expect:

- You have to be there. justOSINT never answers a check itself, and does nothing to hide that the browser is automated: the browser shows its "controlled by automated test software" notice and tells the pages so. A search engine may keep showing checks or refuse altogether; type `s` at the prompt to leave it out.
- The search engines' terms do not allow automated searching. Expect checks, and possibly a temporary block of your network by a search engine. Do not sign in to an account in that window.
- The browser keeps its own profile in a folder named `.justosint` in your home folder, so a consent box you have answered stays answered.
- Bing is first asked for `site:targetdomain.com` alone. Unless most results of that search are on the site (see the table above), it is left out for the run.
- When the browser cannot show a results page (the connection fails, or the answer is an error without a page), justOSINT says so and carries on with the next search, and the summary marks that search engine with one of the remarks above.
- Nothing is saved by the browser. The files are downloaded afterwards, by justOSINT itself, if you ask for that.

### Google

justOSINT's predecessors read Google's result pages with plain web requests. That is no longer possible:

- Since January 2025 Google answers plain web requests with an "enable JavaScript" page that contains no results.
- Since mid-2026 the result pages Google shows in a browser no longer contain the addresses of the results. Each result links to `google.com/goto?url=...`, a redirect that cannot be decoded; the page shows only the name of the site and a shortened path. So there is no list of addresses to copy out of a results page: each address has to be asked of Google's link, one result at a time.
- Google's `robots.txt` disallows both `/search` and `/goto?` for automated clients, and its terms forbid automated queries. Whether to use the browser search with Google is your decision.

`--google-lookup` says how the address of a result is read:

- `click` (the default): each result is clicked in the results page, the way you would click it, and the browser's network log shows where the click leads. No tab is opened, and the window stays on the results: a document is not opened or saved by the click. If a result leads to a web page instead of a document, the window shows that page for a moment and goes back; if going back does not bring the results back, the search is opened once more. The site is asked for the file by this click, as it is when you click a result yourself. Only if a result cannot be clicked (for example because something in the window covers the results) is its link opened in a second tab; justOSINT says so when that happens.
- `request`: Google's link is asked with a plain web request instead, without the browser, and only the redirect is read (the file itself is not asked for). This is the quietest way, and the one that keeps furthest from Google's rules: its `robots.txt` asks programs not to request these links. The requests say that they come from justOSINT unless `--user-agent` is given. If Google does not answer them with a redirect, justOSINT says so and clicks the results instead.
- `off`: results that hide their address are left out. Only results whose address is in the page are kept.

If Google puts a page of its own in the way of a result (a check, or a page with a Continue button), justOSINT waits and asks you to deal with it in the window; there is no time limit. At that prompt, `n` stops reading addresses for the rest of the run and keeps everything found so far, and `s` skips Google. After three results in a row that do not show where they lead, justOSINT stops reading addresses by itself.

The route Google itself offers for searching its index by program is Agent Search (formerly Vertex AI Search) "basic website search". It needs your own Google Cloud project with billing, and Google's terms limit it to domains you own or are authorized to use. justOSINT does not include a client for it.

## Downloads and metadata

- A downloaded file is saved under the last part of its address, and a name with an ending always ends in one of the file types that were asked for. The site cannot choose another ending: `setup.exe?doc=.pdf` is saved as `setup.exe.pdf`, `get.php?file=report.pdf` as `get.php.pdf`, and a name whose ending is no wanted type gets `.download` added. A name without any ending is kept as it is. The wanted types are those of `-t` (`-FileTypes`) in the same run, also with `--list-in` (`-TargetFileList`): to download a list of `.odt` files under their own names, pass `-t odt`.
- Target servers often have expired or self-signed certificates, so certificate errors are ignored while files are downloaded from them (and only then). Copies fetched from the Internet Archive are checked as usual.
- By default only the 'Author' and 'Creator' fields are shown, as these commonly hold usernames and software titles. `--csv FILE` (`-ExtractAllToCsv`) writes all metadata of all files to a CSV instead.
- The values in that CSV come straight from the target's documents, and a spreadsheet program runs a cell that starts with `=`, `+`, `-` or `@` as a formula. justOSINT therefore puts an apostrophe in front of such a cell, which makes it plain text (plain numbers such as `-5` are left as they are). It does the same inside a value after a semicolon, a tab or a line break, because some spreadsheet programs start a new cell there.
- The Python version does not keep a download that the server cuts off (the Internet Archive's copy is tried instead), downloads no file larger than 2 GB, and gives no download longer than 30 minutes.

### ExifTool

justOSINT uses [ExifTool](https://exiftool.org/) by Phil Harvey to read the metadata. It is not shipped in this repository. ExifTool versions before 12.24 can be made to run code by a crafted file (CVE-2021-22204), and justOSINT runs ExifTool on files downloaded from the target, so older versions are refused.

- Windows: run `python -m justosint --install-exiftool` (or `Install-ExifTool` in PowerShell) once. It downloads the current official ExifTool package, checks it against the SHA-256 checksum published on exiftool.org, and puts `exiftool.exe` and its `exiftool_files` folder next to `justOSINT.ps1`.
- Linux or macOS: install it with your package manager, for example `apt install libimage-exiftool-perl` or `brew install exiftool`.

justOSINT looks for ExifTool next to `justOSINT.ps1` and on the PATH. It does not look in the current directory, because that may be a folder of downloaded files.

## Python options

```
-d, --domain          the target domain to search for files
-t, --types           comma separated file extensions (default: "pdf, docx, xlsx, doc, xls, pptx, ppt")
-o, --output-dir      directory to store downloaded files in (default: named after the date and time)
--list-out FILE       write the links that were found to FILE
--list-in FILE        skip searching and use the links in FILE, one per line (no -d is needed with it)
--download            download the files without asking
--extract             extract metadata without asking
--csv FILE            when extracting, write all metadata (not just Author and Creator) to FILE
--user-agent          the User-Agent to send with plain web requests
--max-pages           the most result pages to read per search (default: 5)
--delay               seconds to wait between two requests to the same search engine (default: 2)
--sources             where to look with plain web requests: archive, bing, duckduckgo (default: all three)
--browser [ENGINES]   also search in a browser window: google, bing, duckduckgo (all three if no list is given)
--browser-name        chrome (the default) or edge
--google-lookup HOW   how the address of a Google result is read: click (the default), request or off
--extract-dir DIR     only extract metadata from the files in DIR
--install-exiftool    Windows: download the current ExifTool and put it next to justOSINT
--version             show the version
```

Good to know:

- Unless `--user-agent` says otherwise, the Internet Archive is told that the requests come from justOSINT (`justOSINT/1.0.0`); the search engines and the target site are sent a browser-like User-Agent, as in the PowerShell version.
- Ctrl+C during the search stops the run but keeps what was found: the links are written to the `--list-out` file, or printed if there is none, and can be downloaded later with `--list-in`. Ctrl+C during the downloads keeps the files that were saved by then. Either way the run still ends with the summary.
- Without a terminal (for example in a scheduled job) nothing can be asked: use `--download` and `--extract`, and a question of the browser search is answered in the way that lets the run carry on.
- The exit code is 0 when the run finished (also when it found nothing, or when a search engine or the browser could not be used); 1 when the input was not usable, the output folder or link list could not be written, nothing could be downloaded, or the metadata could not be extracted; 2 for a wrong option; 130 after Ctrl+C.

## PowerShell version

``` PowerShell
C:\> powershell.exe -exec bypass
PS C:\> Import-Module .\justOSINT.ps1
PS C:\> Install-ExifTool
```

`Install-ExifTool` is only needed once.

``` PowerShell
PS C:\> Invoke-JustOSINT -TargetDomain targetdomain.com
PS C:\> Invoke-JustOSINT -TargetDomain targetdomain.com -FileTypes "pdf, xml" -Download -Extract
PS C:\> Invoke-JustOSINT -TargetDomain targetdomain.com -SearchEngines Archive
PS C:\> Invoke-JustOSINT -TargetDomain targetdomain.com -OutputList target-domain-links.txt
PS C:\> Invoke-JustOSINT -TargetDomain targetdomain.com -TargetFileList target-domain-links.txt
PS C:\> Invoke-JustOSINT -TargetDomain targetdomain.com -MaxSearchPages 2 -ExtractAllToCsv all-target-metadata.csv
PS C:\> ExtractMetadata -OutputDir .\2026-11-18-144953\ -ExtractAllToCsv all-target-metadata.csv
```

The PowerShell version asks the Internet Archive, Bing and DuckDuckGo with plain web requests. It does not search Google and has no browser search. `Invoke-JustOSINT` ends with the same summary as the Python version, except when it is stopped with Ctrl+C: PowerShell then ends the function at once and prints nothing more.

```
TargetDomain        - The target domain to search for files.
FileTypes           - A comma separated list of file extensions to search for. By default "pdf, docx, xlsx, doc, xls, pptx, ppt". Downloaded files keep their name only if it ends in one of these types (also with TargetFileList).
OutputList          - A file to output the list of links discovered through web searching to.
OutputDir           - A directory to store all downloaded files in.
TargetFileList      - List of file links to download. When this is given no searching is done.
Download            - Instead of being prompted interactively pass this flag to auto-download files found.
Extract             - Instead of being prompted interactively pass this flag to auto-extract any metadata.
ExtractAllToCsv     - All metadata (not just the default fields) will be extracted from files to a CSV specified with this flag.
UserAgent           - Change the default User Agent.
MaxSearchPages      - The maximum number of pages to search on each search engine.
SearchEngines       - The sources to ask: Bing, DuckDuckGo, Archive (the Internet Archive), or any combination. All three by default.
SearchDelay         - Seconds to wait between two requests to the same search engine. Default 2.
```

### Google API

`justOSINT-API.ps1` searches with Google's Custom Search JSON API. Google has closed this API to new customers and discontinues it on January 1, 2027, so this only works if your Google Cloud project already uses the API, and only until that date.

Search engines created after January 20, 2026 can no longer "search the entire web". They only search the sites on their own "Sites to search" list (up to 50 domains), so the target domain has to be on that list. If it is not, the API answers with no results and no error.

1. Open the Google Cloud project that already uses the Custom Search JSON API (https://console.cloud.google.com/) and check under "APIs & Services" > "Library" that the "Custom Search API" is enabled.
2. Under "APIs & Services" > "Credentials", create an API key.
3. In Google Programmable Search Engine (https://programmablesearchengine.google.com), open or add a search engine, add the target domain under "Sites to search" (`*.targetdomain.com/*` covers its sub-domains too), and copy the Search Engine ID.

Run it with (`justOSINT.ps1` must be in the same folder):

``` PowerShell
.\justOSINT-API.ps1 -TargetDomain "targetdomain.com" -ApiKey "<API key>" -SearchEngineId "<search engine ID>" -ExtractAllToCsv allmetadata.csv
```

The API returns at most 100 results per file type, and the free tier allows 100 queries per day.

## Licence and credits

justOSINT is based on [PowerMeta](https://github.com/dafthack/PowerMeta) by Beau Bullock (@dafthack), and is distributed under the same licence (see `LICENSE`). The idea of a Python version comes from [PyMeta](https://github.com/m8sec/pymeta) by m8sec, of which no code is used. Metadata is read with ExifTool by Phil Harvey.
