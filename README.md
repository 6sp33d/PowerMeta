# PowerMeta
PowerMeta searches for publicly available files hosted on various websites for a particular domain by using specially crafted search engine queries. It then allows for the download of those files from the target domain. After retrieving the files, the metadata associated with them can be analyzed by PowerMeta. Some interesting things commonly found in metadata are usernames, domains, software titles, and computer names.

## Public File Discovery
For many organizations it's common to find publicly available files posted on their external websites. Many times these files contain sensitive information that might be of benefit to an attacker like usernames, domains, software titles or computer names. PowerMeta searches for files on a particular domain using search strings like "site:targetdomain.com filetype:pdf". By default it searches for "pdf, docx, xlsx, doc, xls, pptx, and ppt".

### Which search engines are used

| Search source | State | Notes |
|---|---|---|
| Internet Archive | Used by default | Asks the Wayback Machine's index, a service made to be queried by programs, which addresses it has captured on the domain (up to 10,000). One request covers all file types. It lists files it has seen at any time, so some may be gone from the site; when such a file cannot be downloaded in the same run, PowerMeta fetches the Archive's first copy of it instead. A list saved with `-OutputList` holds only the addresses on the site, so this does not happen with `-TargetFileList`. |
| Bing | Used by default | Bing hides each result behind a redirect link; PowerMeta decodes these. Bing does not always keep to `site:`: when it does not know a site, and when it takes a search for an automated one, it answers with pages of other sites, without saying so. Nothing from another site is ever kept. The Python version first asks Bing for `site:targetdomain.com` alone and leaves Bing out for the run if no result of that search is on the site; the PowerShell version tells you for each file type. |
| DuckDuckGo | Used by default | Uses DuckDuckGo's script-free results page. DuckDuckGo answers scripts from many networks with a bot check or an error instead of results. PowerMeta tells you when that happens and leaves DuckDuckGo alone for the rest of the run. |
| Google result pages | Python version only, in a browser window | Google no longer answers plain web requests, and its result pages no longer contain the addresses of the results. The Python version can run the searches in a browser window that you watch (`--browser`). See [Google](#google). |
| Google Custom Search JSON API (`PowerMeta-API.ps1`) | Existing customers only, until January 1, 2027 | Google has closed this API to new customers and discontinues it on January 1, 2027. See [Google API](#google-api). |

A search engine never lists everything a domain hosts, and what it returns to a script can differ from what it shows in a browser. If you already have a list of links (from a crawl, a sitemap, or a search you did by hand), pass it with `-TargetFileList` and PowerMeta skips searching altogether.

### Google
PowerMeta used to read Google's result pages with plain web requests. That is no longer possible:

- Since January 2025 Google answers plain web requests with an "enable JavaScript" page that contains no results.
- Since mid-2026 the result pages Google shows in a browser no longer contain the addresses of the results. Each result links to `google.com/goto?url=...`, a redirect that cannot be decoded; the page shows only the name of the site and a shortened path. So there is no list of addresses to copy out of a results page: each address has to be asked of Google's link, one result at a time.
- Google's `robots.txt` disallows both `/search` and `/goto?` for automated clients, and its terms forbid automated queries.
- The Custom Search JSON API that `PowerMeta-API.ps1` uses is closed to new customers and ends on January 1, 2027.

What PowerMeta does about it:

- The PowerShell version does not search Google.
- The Python version can run the searches in a browser window with you in front of it (`--browser`, see [Searching In A Browser Window](#searching-in-a-browser-window)). It does not hide that the browser is automated and never answers a check; you do. Whether to use it with Google, given Google's terms, is your decision.
- The route Google itself offers is Agent Search (formerly Vertex AI Search) "basic website search". It searches Google's index for up to 50 domains that you add to a data store, without domain verification, and can be queried with an API key. It needs your own Google Cloud project with billing, is charged per query, and Google's terms limit it to domains you own or are authorized to use. PowerMeta does not include a client for it yet.

## Metadata Extraction
PowerMeta uses [ExifTool](https://exiftool.org/) by Phil Harvey to extract metadata information from files. ExifTool is no longer shipped in this repository: the copy that used to be here (version 10.47 from 2017) can be made to run code by a crafted file (CVE-2021-22204), and PowerMeta runs ExifTool on files downloaded from the target.

On Windows, run `Install-ExifTool` once. It downloads the current official ExifTool package, checks it against the SHA-256 checksum published on exiftool.org, and puts `exiftool.exe` and its `exiftool_files` folder next to `PowerMeta.ps1`. You can also install ExifTool yourself: PowerMeta looks for it next to `PowerMeta.ps1` and on the PATH, and refuses versions older than 12.24. It no longer looks in the current directory, because that may be a folder of downloaded files. On Linux or macOS install it with your package manager.

By default PowerMeta just extracts the 'Author' and 'Creator' fields as these commonly have usernames saved. However all metadata for files can be extracted by passing PowerMeta the -ExtractAllToCsv flag. The values in that CSV come straight from the target's documents, and a spreadsheet program runs a cell that starts with `=`, `+`, `-` or `@` as a formula. PowerMeta therefore puts an apostrophe in front of such a cell, which makes it plain text (plain numbers such as `-5` are left as they are).

A downloaded file is saved under the last part of its address, and the name always ends in one of the file types that were asked for. The site cannot choose another ending: `setup.exe?doc=.pdf` is saved as `setup.exe.pdf`, `get.php?file=report.pdf` as `get.php.pdf`, and a name that matches no wanted type (possible with `-TargetFileList`) gets `.download` added.

Target servers often have expired or self-signed certificates, so certificate errors are ignored while files are downloaded from them (and only then). Copies fetched from the Internet Archive are checked as usual.

## Requirements
- Windows PowerShell 5.1, or PowerShell 7
- ExifTool 12.24 or later for metadata extraction (see above)

## Usage

### Import the Module
```
C:\> powershell.exe -exec bypass
PS C:\> Import-Module .\PowerMeta.ps1
PS C:\> Install-ExifTool
```
`Install-ExifTool` is only needed once.

### Basic Search
This command will ask the Internet Archive, Bing and DuckDuckGo for files on the 'targetdomain.com' domain ending with a file extension of pdf, docx, xlsx, doc, xls, pptx, or ppt. Once it has finished crafting this list it will prompt the user asking if they wish to download the files from the target domain. After downloading files it will prompt again for extraction of metadata from those files.
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com
```
### Changing FileTypes and Automatic Download and Extract
This command will search for files on the 'targetdomain.com' domain ending with a file extension of pdf, or xml. It will then automatically download them from the target domain and extract metadata.
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com -FileTypes "pdf, xml" -Download -Extract
```
### Choosing Sources
This command will only ask the Internet Archive.
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com -SearchEngines Archive
```
### Saving The Links That Were Found
This command will search for files on the 'targetdomain.com' domain and write the links of the files found to disk in a file called "target-domain-links.txt".
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com -OutputList target-domain-links.txt
```
### Downloading Files From A List
This command skips searching and uses the links listed in "target-domain-links.txt" (one per line) instead.
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com -TargetFileList target-domain-links.txt
```
### Extract All Metadata and Limit Page Search
This command will search for files on the 'targetdomain.com' domain but only search the first two pages of results on each search engine. All metadata (not just the default fields) will be saved in a CSV called all-target-metadata.csv.
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com -MaxSearchPages 2 -ExtractAllToCsv all-target-metadata.csv
```
### Extract Metadata From Files In A Directory
This command will simply extract all the metadata from all the files in the folder "\2017-03-031-144953\" and save it in a CSV called all-target-metadata.csv
``` PowerShell
PS C:\> ExtractMetadata -OutputDir .\2017-03-031-144953\ -ExtractAllToCsv all-target-metadata.csv
```

### Google API
`PowerMeta-API.ps1` searches with Google's Custom Search JSON API. Google has closed this API to new customers and discontinues it on January 1, 2027, so this only works if your Google Cloud project already uses the API, and only until that date.

Search engines created after January 20, 2026 can no longer "search the entire web". They only search the sites on their own "Sites to search" list (up to 50 domains), so the target domain has to be on that list. If it is not, the API answers with no results and no error.

Step 1: Open your Google Cloud project
- Go to Google Cloud Console (https://console.cloud.google.com/)
- Select the project that already uses the Custom Search JSON API

Step 2: Check that the Custom Search API is enabled
- In your project, go to "APIs & Services" > "Library"
- Search for "Custom Search API"

Step 3: Create API Key
- Go to "APIs & Services" > "Credentials"
- Click "Create Credentials" > "API Key"
- Copy your API key (you'll need this)

Step 4: Create or edit your Custom Search Engine
- Go to Google Programmable Search Engine (https://programmablesearchengine.google.com)
- Open your search engine, or click "Add a search engine"
- Under "Sites to search", add the target domain (`*.targetdomain.com/*` covers its sub-domains too)
- Copy your Search Engine ID (looks like: 94e6b2cd62344413c)

Run it with (`PowerMeta.ps1` must be in the same folder):
``` PowerShell
.\PowerMeta-API.ps1 -TargetDomain "targetdomain.com" -ApiKey "<API key>" -SearchEngineId "<search engine ID>" -ExtractAllToCsv allmetadata.csv
```
The API returns at most 100 results per file type, and the free tier allows 100 queries per day.

## Python Version
The folder `powermeta` holds the same tool in Python (3.7 or later). It needs no extra packages for a normal run, and it has one thing the PowerShell version does not: it can run the searches in a real browser window.

``` PowerShell
PS C:\> python -m powermeta -d targetdomain.com
PS C:\> python -m powermeta -d targetdomain.com -t "pdf, docx" --download --extract
PS C:\> python -m powermeta -d targetdomain.com --download --extract --csv all-target-metadata.csv
PS C:\> python -m powermeta --extract-dir .\2017-03-031-144953 --csv all-target-metadata.csv
PS C:\> python -m powermeta --install-exiftool
```
Run these from the folder that holds `PowerMeta.ps1`. The options match the PowerShell ones:

```
-d, --domain          - TargetDomain
-t, --types           - FileTypes
-o, --output-dir      - OutputDir
--list-out FILE       - OutputList
--list-in FILE        - TargetFileList (no -d is needed with it)
--download            - Download
--extract             - Extract
--csv FILE            - ExtractAllToCsv
--user-agent          - UserAgent
--max-pages           - MaxSearchPages
--delay               - SearchDelay
--sources             - SearchEngines (archive, bing, duckduckgo)
--browser [ENGINES]   - search in a browser window (google, bing, duckduckgo); see below
--browser-name        - chrome (the default) or edge
--google-lookup HOW   - how the address of a Google result is read: click (the default), request or off; see below
--save-pages DIR      - with --browser: keep a copy of every page that is read, for troubleshooting
--extract-dir DIR     - the same as ExtractMetadata -OutputDir DIR
--install-exiftool    - the same as Install-ExifTool
```
Both versions use the same ExifTool: next to `PowerMeta.ps1`, or on the PATH.

Some things the Python version does differently:
- Unless `--user-agent` says otherwise, the Internet Archive is told that the requests come from PowerMeta (`PowerMeta/2.0.0`); the search engines and the target site are sent a browser-like User-Agent, as in the PowerShell version.
- A download that the server cuts off is not kept (the Internet Archive's copy is tried instead), and no file larger than 2 GB is downloaded.
- Ctrl+C during the search stops the run but keeps what was found: the links are written to the `--list-out` file, or printed if there is none, and can be downloaded later with `--list-in`.
- The exit code is 0 when the run did what was asked, 1 when nothing could be downloaded or the metadata could not be extracted, and 130 after Ctrl+C.

### Searching In A Browser Window
Google no longer answers scripts at all, and Bing and DuckDuckGo turn them away on many networks. With `--browser` the Python version opens a normal Chrome or Edge window and runs the searches there. When a search engine shows a check ("are you a robot"), a consent box or a button that has to be pressed first, PowerMeta stops and asks you to deal with it in the window, then carries on.

``` PowerShell
PS C:\> pip install selenium
PS C:\> python -m powermeta -d targetdomain.com --browser
PS C:\> python -m powermeta -d targetdomain.com --browser google --browser-name edge
```
`--browser` alone uses Google, Bing and DuckDuckGo. The Internet Archive is still asked as well; `--sources` controls that.

What to expect:
- You have to be there. PowerMeta never answers a check itself, and does nothing to hide that the browser is automated: the browser shows its "controlled by automated test software" notice and tells the pages so. A search engine may keep showing checks or refuse altogether; type `s` at the prompt to leave it out.
- The search engines' terms do not allow automated searching. Expect checks, and possibly a temporary block of your network by a search engine. Do not sign in to an account in that window.
- The browser keeps its own profile in a folder named `.powermeta` in your home folder, so a consent box you have answered stays answered.
- Bing is first asked for `site:targetdomain.com` alone. If it answers with pages of other sites only (see the table above), it is left out for the run.
- The browser mode is tested against local stand-ins for the three search engines. How the real ones react differs between networks and changes over time; `--save-pages DIR` keeps a copy of every page PowerMeta read, which shows what it was given. A saved search page can hold your IP address or location.

#### Google's hidden addresses
A Google result does not show its address (see [Google](#google)), so PowerMeta has to ask Google's link where it leads, one result at a time. `--google-lookup` says how:

- `click` (the default): each result is clicked in the results page, the way you would click it, and the browser's network log shows where the click leads. No tab is opened, and the window stays on the results: a document is not opened or saved by the click. If a result leads to a web page instead of a document, the window shows that page for a moment and goes back. The site is asked for the file by this click, as it is when you click a result yourself. Only if a result cannot be clicked is its link opened in a second tab.
- `request`: Google's link is asked with a plain web request instead, without the browser, and only the redirect is read (the file itself is not asked for). This is the quietest way, and the one that keeps furthest from Google's rules: its `robots.txt` asks programs not to request these links. The requests say that they come from PowerMeta unless `--user-agent` is given. If Google does not answer them with a redirect, PowerMeta says so and clicks the results instead.
- `off`: results that hide their address are left out. Only results whose address is in the page are kept.

If Google puts a page of its own in the way of a result (a check, or a page with a Continue button), PowerMeta waits and asks you to deal with it in the window; there is no time limit. At that prompt, `n` stops reading addresses for the rest of the run and keeps everything found so far, and `s` skips Google. After three results in a row that do not show where they lead, PowerMeta stops reading addresses by itself.

### Tests
``` PowerShell
PS C:\> python -m unittest discover -s tests -t .
```
The tests use a local stand-in server and never contact the real services. The browser tests need Selenium and Chrome and only run when asked for:
``` PowerShell
PS C:\> $env:POWERMETA_BROWSER_TESTS = "1"
PS C:\> python -m unittest tests.test_browser
```

## Checking Your Setup
`Test-PowerMeta.ps1` checks the helper functions without touching the network, and tests metadata extraction on two small PDF files it creates itself.
``` PowerShell
PS C:\> .\Test-PowerMeta.ps1
```
Add `-Live` to also run one real Bing search, one real DuckDuckGo search and one real Internet Archive lookup, which shows what each of them returns from your computer.
``` PowerShell
PS C:\> .\Test-PowerMeta.ps1 -Live -TargetDomain targetdomain.com
```

## PowerMeta Options
```
TargetDomain        - The target domain to search for files.
FileTypes           - A comma seperated list of file extensions to search for. By default PowerMeta searches for "pdf, docx, xlsx, doc, xls, pptx, ppt".
OutputList          - A file to output the list of links discovered through web searching to.
OutputDir           - A directory to store all downloaded files in.
TargetFileList      - List of file links to download. When this is given no searching is done.
Download            - Instead of being prompted interactively pass this flag to auto-download files found.
Extract             - Instead of being prompted interactively pass this flag to extract metadata from found files pass this flag to auto-extract any metadata.
ExtractAllToCsv     - All metadata (not just the default fields) will be extracted from files to a CSV specified with this flag.
UserAgent           - Change the default User Agent used by PowerMeta.
MaxSearchPages      - The maximum number of pages to search on each search engine.
SearchEngines       - The sources to ask: Bing, DuckDuckGo, Archive (the Internet Archive), or any combination. All three by default.
SearchDelay         - Seconds to wait between two requests to the same search engine. Default 2.
```
