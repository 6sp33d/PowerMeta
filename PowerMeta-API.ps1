<#
By Beau Bullock (@dafthack)
.SYNOPSIS
    PowerMeta-API - Find files hosted on target domains using Google Custom Search API

.DESCRIPTION
    This script uses Google's Custom Search API to find publicly available files (PDF, DOCX, XLSX, etc.)
    hosted on a target domain. It's more reliable than web scraping and doesn't trigger anti-bot measures.

    IMPORTANT: Google has closed the Custom Search JSON API to new customers and discontinues it on
    January 1, 2027. Search engines created after January 20, 2026 can no longer "search the entire
    web"; they only search the sites on their own "Sites to search" list, so the target domain has to
    be added to that list. See the README for what to use instead.

    PowerMeta.ps1 must be in the same folder as this script (it holds the download and metadata code).

.PARAMETER TargetDomain
    The target domain to search for files (e.g., "example.com")

.PARAMETER ApiKey
    Your Google Custom Search API key

.PARAMETER SearchEngineId
    Your Google Custom Search Engine ID

.PARAMETER FileTypes
    Comma-separated list of file extensions to search for
    Default: "pdf,docx,xlsx,doc,xls,pptx,ppt"

.PARAMETER MaxResults
    Maximum number of results to return per file type (default: 100, which is also the most the API returns)

.PARAMETER OutputFile
    Optional file to save the results to

.PARAMETER OutputDir
    Optional directory to save downloaded files

.PARAMETER Download
    Switch to enable downloading files

.PARAMETER Extract
    Switch to enable extracting metadata from files

.PARAMETER ExtractAllToCsv
    Optional file to save extracted metadata to

.PARAMETER UserAgent
    Optional User-Agent string for downloading files

.PARAMETER ShowUrls
    Switch to enable verbose output showing URLs returned by API

.EXAMPLE
    .\PowerMeta-API.ps1 -TargetDomain "example.com" -ApiKey "YOUR_API_KEY" -SearchEngineId "YOUR_SEARCH_ENGINE_ID"

.EXAMPLE
    .\PowerMeta-API.ps1 -TargetDomain "example.com" -ApiKey "YOUR_API_KEY" -SearchEngineId "YOUR_SEARCH_ENGINE_ID" -FileTypes "pdf,xlsx" -MaxResults 50 -OutputFile "results.txt"

.EXAMPLE
    .\PowerMeta-API.ps1 -TargetDomain "example.com" -ApiKey "YOUR_API_KEY" -SearchEngineId "YOUR_SEARCH_ENGINE_ID" -Download -OutputDir "downloads"

.EXAMPLE
    .\PowerMeta-API.ps1 -TargetDomain "example.com" -ApiKey "YOUR_API_KEY" -SearchEngineId "YOUR_SEARCH_ENGINE_ID" -Download -Extract -OutputDir "downloads"

.EXAMPLE
    .\PowerMeta-API.ps1 -TargetDomain "example.com" -ApiKey "YOUR_API_KEY" -SearchEngineId "YOUR_SEARCH_ENGINE_ID" -Download -Extract -ExtractAllToCsv "metadata.csv" -OutputDir "downloads"

.NOTES
    Setup Instructions (only possible for projects that already use the Custom Search JSON API):
    1. Go to https://console.cloud.google.com/
    2. Select your existing project
    3. Check that the "Custom Search API" is enabled
    4. Create credentials (API Key)
    5. Go to https://programmablesearchengine.google.com/
    6. Open your search engine, or create a new one
    7. Add the target domain to "Sites to search" (new engines can no longer search the entire web)
    8. Get your Search Engine ID

    Keep this file plain ASCII: Windows PowerShell 5.1 misreads other characters in script files.
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$TargetDomain,

    [Parameter(Mandatory = $true, Position = 1)]
    [string]$ApiKey,

    [Parameter(Mandatory = $true, Position = 2)]
    [string]$SearchEngineId,

    [Parameter(Mandatory = $false)]
    [string]$FileTypes = "pdf,docx,xlsx,doc,xls,pptx,ppt",

    [Parameter(Mandatory = $false)]
    [int]$MaxResults = 100,

    [Parameter(Mandatory = $false)]
    [string]$OutputFile = "",

    [Parameter(Mandatory = $false)]
    [string]$OutputDir = "",

    [Parameter(Mandatory = $false)]
    [switch]$Download,

    [Parameter(Mandatory = $false)]
    [switch]$Extract,

    [Parameter(Mandatory = $false)]
    [string]$ExtractAllToCsv = "",

    [Parameter(Mandatory = $false)]
    [string]$UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",

    [Parameter(Mandatory = $false)]
    [switch]$ShowUrls
)

# The download and metadata functions are shared with PowerMeta.ps1
$powerMetaCore = Join-Path $PSScriptRoot 'PowerMeta.ps1'
if (-not (Test-Path -LiteralPath $powerMetaCore -PathType Leaf)) {
    Write-Host "[!] PowerMeta.ps1 was not found in $PSScriptRoot. Keep PowerMeta.ps1 and PowerMeta-API.ps1 in the same folder."
    exit 1
}
. $powerMetaCore

# Number of API requests that failed during this run
$apiErrors = 0

# Turns a failed API call into one readable line, with a hint for the common causes
function Get-ApiErrorText {
    param($ErrorRecord)

    $status = 0
    try { $status = [int]$ErrorRecord.Exception.Response.StatusCode } catch { $status = 0 }

    # Google answers errors with JSON: { "error": { "code": 403, "message": "..." } }
    $detail = ''
    try {
        if ($ErrorRecord.ErrorDetails -and $ErrorRecord.ErrorDetails.Message) {
            $body = ConvertFrom-Json -InputObject $ErrorRecord.ErrorDetails.Message
            if ($body.error -and $body.error.message) { $detail = [string]$body.error.message }
        }
    }
    catch { $detail = '' }
    if ($detail -eq '') { $detail = [string]$ErrorRecord.Exception.Message }

    $hint = ''
    switch ($status) {
        400 { $hint = ' Check the Search Engine ID.' }
        403 { $hint = ' Either the Custom Search API is not enabled for the project this key belongs to, the key is restricted, or the project is not an existing customer (Google closed this API to new customers).' }
        429 { $hint = ' The query quota is used up (the free tier allows 100 queries per day).' }
    }

    if ($status -gt 0) { return "HTTP $status - $detail$hint" }
    return "$detail$hint"
}

# Function to search for files using Google Custom Search API
function Search-FilesOnDomain {
    param(
        [string]$Domain,
        [string]$FileType,
        [string]$ApiKey,
        [string]$SearchEngineId,
        [int]$MaxResults
    )

    Write-Host "[*] Searching for $FileType files on $Domain..."

    # The API never returns more than 100 results per query (10 per request) and rejects requests beyond that
    $apiLimit = 100
    $wanted = [Math]::Max(1, [Math]::Min($MaxResults, $apiLimit))
    $results = @()
    $startIndex = 1
    $encodedQuery = [System.Uri]::EscapeDataString("site:$Domain filetype:$FileType")
    $encodedKey = [System.Uri]::EscapeDataString($ApiKey)
    $encodedEngine = [System.Uri]::EscapeDataString($SearchEngineId)

    while ($results.Count -lt $wanted -and $startIndex -le $apiLimit) {
        $num = [Math]::Min(10, [Math]::Min($wanted - $results.Count, $apiLimit - $startIndex + 1))
        $apiUrl = "https://www.googleapis.com/customsearch/v1?key=$encodedKey&cx=$encodedEngine&q=$encodedQuery&start=$startIndex&num=$num"

        Write-Host "  [*] Requesting results $startIndex to $($startIndex + $num - 1)..."

        try {
            $response = Invoke-RestMethod -Uri $apiUrl -Method Get -TimeoutSec 120 -ErrorAction Stop
        }
        catch {
            # Keep what has been collected so far instead of throwing it away
            Write-Host "  [!] Request failed: $(Get-ApiErrorText $_)"
            $script:apiErrors++
            break
        }

        $items = @($response.items | Where-Object { $null -ne $_ })
        if ($items.Count -eq 0) {
            Write-Host "  [*] No more results found"
            break
        }

        if ($ShowUrls) {
            Write-Host "  [*] Debug: URLs returned by API:"
        }
        foreach ($item in $items) {
            if ($ShowUrls) {
                Write-Host "    $($item.link)"
            }
            # Accept all results from the API since filetype: parameter should already filter correctly
            $results += [string]$item.link
        }
        Write-Host "  [*] Found $($items.Count) results in this batch"

        # The response names the next page when there is one
        $previousStart = $startIndex
        if ($response.queries) {
            $nextPage = @($response.queries.nextPage | Where-Object { $null -ne $_ })
            if ($nextPage.Count -eq 0) { break }
            $startIndex = [int]$nextPage[0].startIndex
        }
        else {
            if ($items.Count -lt $num) { break }
            $startIndex += $num
        }
        if ($startIndex -le $previousStart) { break }

        # Add small delay between API requests
        Start-Sleep -Milliseconds 500
    }

    Write-Host "[*] Total $FileType files found: $($results.Count)"
    return $results
}

# Function to validate API credentials
function Test-APICredentials {
    param(
        [string]$ApiKey,
        [string]$SearchEngineId
    )

    Write-Host "[*] Testing API credentials..."
    $testUrl = "https://www.googleapis.com/customsearch/v1?key=$([System.Uri]::EscapeDataString($ApiKey))&cx=$([System.Uri]::EscapeDataString($SearchEngineId))&q=test&num=1"

    try {
        $response = Invoke-RestMethod -Uri $testUrl -Method Get -TimeoutSec 20 -ErrorAction Stop
    }
    catch {
        Write-Host "[!] API credentials test failed: $(Get-ApiErrorText $_)"
        return $false
    }

    if ($response.searchInformation) {
        Write-Host "[+] API credentials are valid!"
        return $true
    }

    Write-Host "[!] API response format unexpected"
    return $false
}

# Asks a yes/no question and returns $true for yes
function Read-YesNo {
    param([string]$Title, [string]$Message, [string]$YesHelp, [string]$NoHelp)

    $yes = New-Object System.Management.Automation.Host.ChoiceDescription "&Yes", $YesHelp
    $no = New-Object System.Management.Automation.Host.ChoiceDescription "&No", $NoHelp
    $options = [System.Management.Automation.Host.ChoiceDescription[]]($yes, $no)
    return ($host.ui.PromptForChoice($Title, $Message, $options, 0) -eq 0)
}

# Main execution
Write-Host "=========================================="
Write-Host "PowerMeta-API - Google Custom Search Tool"
Write-Host "=========================================="
Write-Host ""

if ((Get-Date) -ge [datetime]'2027-01-01') {
    Write-Host "[!] Google discontinued the Custom Search JSON API on January 1, 2027. This script can only work if Google has extended that date. See the README for the alternatives."
}
else {
    Write-Host "[!] Note: Google discontinues the Custom Search JSON API on January 1, 2027 and has closed it to new customers. See the README for the alternatives."
}
Write-Host ""

# Validate API credentials
if (-not (Test-APICredentials -ApiKey $ApiKey -SearchEngineId $SearchEngineId)) {
    Write-Host "[!] Invalid API credentials. Please check your API key and Search Engine ID."
    Write-Host "[!] See the script header for setup instructions."
    exit 1
}

# Parse the target domain and file types
$domain = Get-PowerMetaHostName $TargetDomain
$fileTypeArray = @(Get-PowerMetaFileTypeList $FileTypes)
if ($domain -eq '' -or $fileTypeArray.Count -eq 0) {
    Write-Host "[!] Nothing to search for. Check -TargetDomain and -FileTypes."
    exit 1
}
if ($MaxResults -gt 100) {
    Write-Host "[*] The API returns at most 100 results per file type, so -MaxResults $MaxResults is treated as 100."
    $MaxResults = 100
}

Write-Host "[*] Target Domain: $domain"
Write-Host "[*] File Types: $($fileTypeArray -join ', ')"
Write-Host "[*] Max Results per type: $MaxResults"
Write-Host ""

# Search for each file type
$allResults = @()

foreach ($fileType in $fileTypeArray) {
    $found = @(Search-FilesOnDomain -Domain $domain -FileType $fileType -ApiKey $ApiKey -SearchEngineId $SearchEngineId -MaxResults $MaxResults)
    $allResults += $found
    Write-Host ""
}

# Remove duplicates and sort
$uniqueResults = @($allResults | Where-Object { $_ } | Sort-Object -Unique)

Write-Host "=========================================="
Write-Host "SEARCH COMPLETE"
Write-Host "=========================================="
Write-Host "[*] Total unique files found: $($uniqueResults.Count)"
Write-Host ""

if ($uniqueResults.Count -eq 0) {
    Write-Host "[*] No files found for the specified criteria."
    if ($apiErrors -gt 0) {
        Write-Host "[!] $apiErrors API requests failed (see above), so this is not a reliable 'nothing there'."
    }
    else {
        Write-Host "[*] If you expected results: a search engine created after January 20, 2026 only searches the sites on its own 'Sites to search' list. Add $domain to that list at https://programmablesearchengine.google.com/ and run this again."
    }
    return
}

# Display results
Write-Host "Files found:"
Write-Host "------------"
foreach ($result in $uniqueResults) {
    Write-Host $result
}
if ($apiErrors -gt 0) {
    Write-Host ""
    Write-Host "[!] $apiErrors API requests failed (see above), so this list may be incomplete."
}

# Save to file if specified
if ($OutputFile -ne "") {
    try {
        $uniqueResults | Out-File -FilePath $OutputFile -Encoding ascii -ErrorAction Stop
        Write-Host ""
        Write-Host "[+] Results saved to: $OutputFile"
    }
    catch {
        Write-Host "[!] Error saving to file: $($_.Exception.Message)"
    }
}

# Handle download functionality
$doDownload = [bool]$Download
if (-not $doDownload) {
    $doDownload = Read-YesNo -Title "Download Files?" -Message "Would you like to download all of the files discovered?" -YesHelp "Downloads all of the target files." -NoHelp "The files will not be downloaded."
}

if ($doDownload) {
    if ($OutputDir -eq "") {
        $OutputDir = Get-Date -Format "yyyy-MM-dd-HHmmss"
    }
    Write-Host "[*] Now downloading the files."
    Get-TargetFiles -targetlinks $uniqueResults -OutputDir $OutputDir -UserAgent $UserAgent
}
else {
    Write-Host "[*] No files will be downloaded."
}

# Handle metadata extraction
if ($OutputDir -eq "" -or -not (Test-Path -LiteralPath $OutputDir -PathType Container)) {
    if ($Extract) {
        Write-Host "[!] There is no folder of downloaded files to extract metadata from. Skipping."
    }
    return
}

$doExtract = [bool]$Extract
if (-not $doExtract) {
    $doExtract = Read-YesNo -Title "Extract Metadata?" -Message "Would you like to extract metadata from all of the files downloaded now?" -YesHelp "Extracts metadata from downloaded files." -NoHelp "No metadata will be extracted at this time."
}

if ($doExtract) {
    Write-Host "[*] Now extracting metadata from the files."
    ExtractMetadata -OutputDir $OutputDir -ExtractAllToCsv $ExtractAllToCsv
}
else {
    Write-Host "[*] No metadata will be extracted at this time."
    Write-Host "[*] If you wish to extract metadata later, load PowerMeta.ps1 and run:"
    Write-Host "    ExtractMetadata -OutputDir `"$OutputDir`""
}
