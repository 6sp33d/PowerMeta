# PowerMeta
PowerMeta searches for publicly available files hosted on various websites for a particular domain by using specially crafted search engine queries. It then allows for the download of those files from the target domain. After retrieving the files, the metadata associated with them can be analyzed by PowerMeta. Some interesting things commonly found in metadata are usernames, domains, software titles, and computer names.

## Public File Discovery
For many organizations it's common to find publicly available files posted on their external websites. Many times these files contain sensitive information that might be of benefit to an attacker like usernames, domains, software titles or computer names. PowerMeta searches for files on a particular domain using search strings like "site:targetdomain.com filetype:pdf". By default it searches for "pdf, docx, xlsx, doc, xls, pptx, and ppt".

### Which search engines are used

| Search source | State | Notes |
|---|---|---|
| Bing | Used by default | Bing hides each result behind a redirect link; PowerMeta decodes these. Bing sometimes answers automated `site:` + `filetype:` queries with unrelated pages. PowerMeta tells you when none of the results are on the target domain. |
| DuckDuckGo | Used by default | Uses DuckDuckGo's script-free results page. DuckDuckGo shows some networks a bot check instead of results. PowerMeta tells you when that happens and leaves DuckDuckGo alone for the rest of the run. |
| Google result pages | Not searched | Google no longer gives result pages, or the addresses in them, to automated clients. See [Google](#google). |
| Google Custom Search JSON API (`PowerMeta-API.ps1`) | Existing customers only, until January 1, 2027 | Google has closed this API to new customers and discontinues it on January 1, 2027. See [Google API](#google-api). |

A search engine never lists everything a domain hosts, and what it returns to a script can differ from what it shows in a browser. If you already have a list of links (from a crawl, a sitemap, or a search you did by hand), pass it with `-TargetFileList` and PowerMeta skips searching altogether.

### Google
PowerMeta used to read Google's result pages. That is no longer possible, and PowerMeta does not try to work around it:

- Since January 2025 Google answers plain web requests with an "enable JavaScript" page that contains no results.
- Since mid-2026 the result pages Google shows in a browser no longer contain the addresses of the results. Each result links to `google.com/goto?url=...`, a redirect that cannot be decoded.
- Google's `robots.txt` disallows both `/search` and `/goto?` for automated clients, and its terms forbid automated queries.
- The Custom Search JSON API that `PowerMeta-API.ps1` uses is closed to new customers and ends on January 1, 2027.

The route Google itself offers after that date is Agent Search (formerly Vertex AI Search) "basic website search". It searches Google's index for up to 50 domains that you add to a data store, without domain verification, and can be queried with an API key. It needs your own Google Cloud project with billing, is charged per query, and Google's terms limit it to domains you own or are authorized to use. PowerMeta does not include a client for it yet.

## Metadata Extraction
PowerMeta uses [ExifTool](https://exiftool.org/) by Phil Harvey to extract metadata information from files. ExifTool is no longer shipped in this repository: the copy that used to be here (version 10.47 from 2017) can be made to run code by a crafted file (CVE-2021-22204), and PowerMeta runs ExifTool on files downloaded from the target.

On Windows, run `Install-ExifTool` once. It downloads the current official ExifTool package, checks it against the SHA-256 checksum published on exiftool.org, and puts `exiftool.exe` and its `exiftool_files` folder next to `PowerMeta.ps1`. You can also install ExifTool yourself: PowerMeta looks for it next to `PowerMeta.ps1`, in the current directory, and on the PATH, and refuses versions older than 12.24. On Linux or macOS install it with your package manager.

By default PowerMeta just extracts the 'Author' and 'Creator' fields as these commonly have usernames saved. However all metadata for files can be extracted by passing PowerMeta the -ExtractAllToCsv flag.

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
This command will search Bing and DuckDuckGo for files on the 'targetdomain.com' domain ending with a file extension of pdf, docx, xlsx, doc, xls, pptx, or ppt. Once it has finished crafting this list it will prompt the user asking if they wish to download the files from the target domain. After downloading files it will prompt again for extraction of metadata from those files.
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com
```
### Changing FileTypes and Automatic Download and Extract
This command will search for files on the 'targetdomain.com' domain ending with a file extension of pdf, or xml. It will then automatically download them from the target domain and extract metadata.
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com -FileTypes "pdf, xml" -Download -Extract
```
### Choosing Search Engines
This command will only ask DuckDuckGo.
``` PowerShell
PS C:\> Invoke-PowerMeta -TargetDomain targetdomain.com -SearchEngines DuckDuckGo
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

## Checking Your Setup
`Test-PowerMeta.ps1` checks the helper functions without touching the network, and tests metadata extraction on two small PDF files it creates itself.
``` PowerShell
PS C:\> .\Test-PowerMeta.ps1
```
Add `-Live` to also run one real Bing search and one real DuckDuckGo search, which shows what the search engines return from your computer.
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
SearchEngines       - The search engines to ask: Bing, DuckDuckGo, or both (the default).
SearchDelay         - Seconds to wait between two requests to the same search engine. Default 2.
```
