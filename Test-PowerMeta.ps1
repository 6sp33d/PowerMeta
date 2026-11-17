<#
.SYNOPSIS
    Self-test for PowerMeta. Checks the helper functions without touching the network, and tests
    metadata extraction on two small PDF files it creates itself (if ExifTool is installed).

.PARAMETER Live
    Also run one real Bing search and one real DuckDuckGo search, to see what the search engines
    return from this computer.

.PARAMETER TargetDomain
    The domain to use for the -Live search.

.EXAMPLE
    .\Test-PowerMeta.ps1

.EXAMPLE
    .\Test-PowerMeta.ps1 -Live -TargetDomain targetdomain.com

.NOTES
    Keep this file plain ASCII: Windows PowerShell 5.1 misreads other characters in script files.
#>

param(
    [switch]$Live,

    [string]$TargetDomain = "microsoft.com"
)

. (Join-Path $PSScriptRoot 'PowerMeta.ps1')

$failures = 0

function Assert-Equal {
    param([string]$Name, $Actual, $Expected)

    if (([string]$Actual) -ceq ([string]$Expected)) {
        Write-Host "[ok]   $Name"
    }
    else {
        Write-Host "[FAIL] $Name - expected '$Expected' but got '$Actual'"
        $script:failures++
    }
}

# Writes a minimal one-page PDF with the given Author and Creator
function New-TestPdf {
    param([string]$Path, [string]$Author, [string]$Creator)

    $objects = @(
        '<< /Type /Catalog /Pages 2 0 R >>',
        '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>',
        ('<< /Author (' + $Author + ') /Creator (' + $Creator + ') >>')
    )
    $nl = "`n"
    $body = '%PDF-1.4' + $nl
    $offsets = @()
    for ($i = 0; $i -lt $objects.Count; $i++) {
        $offsets += $body.Length
        $body += ('{0} 0 obj' -f ($i + 1)) + $nl + $objects[$i] + $nl + 'endobj' + $nl
    }
    $xref = $body.Length
    $body += 'xref' + $nl + ('0 {0}' -f ($objects.Count + 1)) + $nl + '0000000000 65535 f ' + $nl
    foreach ($offset in $offsets) {
        $body += ('{0:D10} 00000 n ' -f $offset) + $nl
    }
    $body += 'trailer' + $nl + ('<< /Size {0} /Root 1 0 R /Info 4 0 R >>' -f ($objects.Count + 1)) + $nl + 'startxref' + $nl + $xref + $nl + '%%EOF' + $nl
    [System.IO.File]::WriteAllText($Path, $body, [System.Text.Encoding]::ASCII)
}

Write-Host "PowerShell $($PSVersionTable.PSVersion)"
Write-Host ""
Write-Host "Search result handling"

Assert-Equal 'Bing link is unwrapped' (ConvertFrom-PowerMetaBingLink 'https://www.bing.com/ck/a?!&&p=abc123&ptn=3&ver=2&hsh=4&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9hL3JlcG9ydC5wZGY&ntb=1') 'https://example.com/a/report.pdf'
Assert-Equal 'Bing link with a query string is unwrapped' (ConvertFrom-PowerMetaBingLink 'https://www.bing.com/ck/a?!&&p=abc123&u=a1aHR0cHM6Ly93d3cuZXhhbXBsZS5jb20vZG9jcy9Bbm51YWwlMjBSZXBvcnQuZG9jeD9kb3dubG9hZD0xJnY9Mg&ntb=1') 'https://www.example.com/docs/Annual%20Report.docx?download=1&v=2'
Assert-Equal 'Bing link needing padding is unwrapped' (ConvertFrom-PowerMetaBingLink 'https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9hYQ') 'https://example.com/aa'
Assert-Equal 'A link that is not wrapped is left alone' (ConvertFrom-PowerMetaBingLink 'https://example.com/a/report.pdf') 'https://example.com/a/report.pdf'
Assert-Equal 'A wrapped link without an address gives nothing' (ConvertFrom-PowerMetaBingLink 'https://www.bing.com/ck/a?!&&p=abc123') ''

Assert-Equal 'Bing link to one of its own pages gives nothing' (ConvertFrom-PowerMetaBingLink 'https://www.bing.com/ck/a?u=a1L2ltYWdlcy9zZWFyY2g_cT14') ''

Assert-Equal 'DuckDuckGo link is unwrapped' (ConvertFrom-PowerMetaDuckDuckGoLink '//duckduckgo.com/l/?uddg=https%3A%2F%2Ffiles.example.com%2Fa%2Fbudget%2D2025.pdf%3Fdl%3D1&rut=0123abcd') 'https://files.example.com/a/budget-2025.pdf?dl=1'
Assert-Equal 'A plain DuckDuckGo result is left alone' (ConvertFrom-PowerMetaDuckDuckGoLink 'https://www.example.com/docs/Annual%20Report.pdf') 'https://www.example.com/docs/Annual%20Report.pdf'

$ddgPage = '<div class="nav-link"><form action="/html/" method="post"><input type="submit" value="Previous" /><input type="hidden" name="q" value="site:example.com filetype:pdf" /><input type="hidden" name="s" value="0" /></form></div><div class="nav-link"><form action="/html/" method="post"><input type="submit" value="Next" /><input type="hidden" name="q" value="site:example.com filetype:pdf" /><input type="hidden" name="s" value="60" /><input type="hidden" name="vqd" value="4-222&amp;x" /></form></div>'
$nextForm = Get-PowerMetaDuckDuckGoNextForm -Html $ddgPage -After 30
$nextOffset = ''
$nextToken = ''
if ($nextForm) {
    $nextOffset = $nextForm['s']
    $nextToken = $nextForm['vqd']
}
Assert-Equal 'DuckDuckGo "Next" form is found' $nextOffset '60'
Assert-Equal 'Its fields are decoded' $nextToken '4-222&x'
Assert-Equal 'There is no "Next" form on the last page' (Get-PowerMetaDuckDuckGoNextForm -Html $ddgPage -After 60) ''

Assert-Equal 'Links are read from HTML' ((@(Get-PowerMetaLinksFromHtml '<p><a class="x" href="https://example.com/a.pdf?x=1&amp;y=2">one</a> <A HREF=''/relative''>two</A> <a href=bare.html>three</a></p>')) -join '|') 'https://example.com/a.pdf?x=1&y=2|/relative|bare.html'

Assert-Equal 'File on the domain is accepted' (Test-PowerMetaFileLink -Link 'https://example.com/x/report.pdf' -Domain 'example.com' -FileType 'pdf') $true
Assert-Equal 'File on a sub-domain is accepted' (Test-PowerMetaFileLink -Link 'http://files.example.com/x/REPORT.PDF?dl=1' -Domain 'example.com' -FileType 'pdf') $true
Assert-Equal 'File named in the query string is accepted' (Test-PowerMetaFileLink -Link 'https://example.com/get.php?f=a.pdf&t=1' -Domain 'example.com' -FileType 'pdf') $true
Assert-Equal 'docx is not reported as doc' (Test-PowerMetaFileLink -Link 'https://example.com/a.docx' -Domain 'example.com' -FileType 'doc') $false
Assert-Equal 'Other domain is rejected' (Test-PowerMetaFileLink -Link 'https://notexample.com/a.pdf' -Domain 'example.com' -FileType 'pdf') $false
Assert-Equal 'Look-alike domain is rejected' (Test-PowerMetaFileLink -Link 'https://example.com.evil.org/a.pdf' -Domain 'example.com' -FileType 'pdf') $false
Assert-Equal 'Relative link is rejected' (Test-PowerMetaFileLink -Link '/search?q=site%3aexample.com+filetype%3apdf' -Domain 'example.com' -FileType 'pdf') $false
Assert-Equal 'Page without the extension is rejected' (Test-PowerMetaFileLink -Link 'https://example.com/pdf/list' -Domain 'example.com' -FileType 'pdf') $false

Write-Host ""
Write-Host "Input handling"

Assert-Equal 'File types are split and cleaned' ((@(Get-PowerMetaFileTypeList 'pdf, .DOCX,xlsx,,pdf')) -join '|') 'pdf|docx|xlsx'
Assert-Equal 'Domain is taken from a URL' (Get-PowerMetaHostName ' HTTPS://www.TargetDomain.com/path?q=1 ') 'www.targetdomain.com'
Assert-Equal 'Wildcard domain is accepted' (Get-PowerMetaHostName '*.targetdomain.com') 'targetdomain.com'

Write-Host ""
Write-Host "File names for downloads"

Assert-Equal 'Plain name is kept' (Get-PowerMetaFileName 'https://example.com/a/report.pdf') 'report.pdf'
Assert-Equal 'Query string is dropped' (Get-PowerMetaFileName 'https://example.com/a/report.pdf?x=1') 'report.pdf'
Assert-Equal 'Missing name becomes download' (Get-PowerMetaFileName 'https://example.com/a/') 'download'
Assert-Equal 'PowerShell syntax in a name is defused' (Get-PowerMetaFileName 'https://example.com/x/$(calc);.pdf') '_(calc)_.pdf'
Assert-Equal 'Reserved device name is changed' (Get-PowerMetaFileName 'https://example.com/con.pdf') '_con.pdf'
Assert-Equal 'A download cannot be named like ExifTool' (Get-PowerMetaFileName 'https://example.com/a.pdf/exiftool.exe') '_exiftool.exe'
Assert-Equal 'A download cannot be named like the ExifTool configuration file' (Get-PowerMetaFileName 'https://example.com/x/.ExifTool_config') '_.ExifTool_config'
Assert-Equal 'A download cannot look like a command-line option' (Get-PowerMetaFileName 'https://example.com/x/-o.pdf') '_-o.pdf'

Write-Host ""
Write-Host "Metadata extraction"

$exiftool = Get-PowerMetaExifTool
if (-not $exiftool) {
    Write-Host "[skip] ExifTool is not installed, so metadata extraction was not tested. Run Install-ExifTool, then run this again."
}
elseif (-not (Test-PowerMetaExifTool $exiftool)) {
    Write-Host "[skip] The ExifTool that was found cannot be used (see the message above)."
}
else {
    Write-Host "[info] Using $exiftool"
    $folder = Join-Path ([System.IO.Path]::GetTempPath()) ('powermeta-selftest-' + [System.Guid]::NewGuid().ToString('N'))
    $files = Join-Path $folder 'files with space'
    New-Item -ItemType Directory -Path $files -Force | Out-Null
    try {
        New-TestPdf -Path (Join-Path $files 'one.pdf') -Author 'jsmith' -Creator 'Writer, version 1'
        # A file name that the old Invoke-Expression code would have run as a command
        New-TestPdf -Path (Join-Path $files 'two$(x);.pdf') -Author 'adoe' -Creator 'Writer, version 1'

        $output = @(ExtractMetadata -OutputDir $files)
        Assert-Equal 'Both authors are found' (($output -contains 'jsmith') -and ($output -contains 'adoe')) $true
        Assert-Equal 'A value containing a comma stays in one piece' ($output -contains 'Writer, version 1') $true

        $csv = Join-Path $folder 'all.csv'
        ExtractMetadata -OutputDir $files -ExtractAllToCsv $csv | Out-Null
        $rows = @()
        if (Test-Path -LiteralPath $csv) { $rows = @(Import-Csv -LiteralPath $csv) }
        Assert-Equal 'ExtractAllToCsv writes one row per file' $rows.Count 2
    }
    finally {
        Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($Live) {
    $liveDomain = Get-PowerMetaHostName $TargetDomain
    # The same agent string Invoke-PowerMeta sends by default
    $liveAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"

    Write-Host ""
    Write-Host "Live search (one Bing query for PDF files on $liveDomain)"
    $links = @(Search-PowerMetaBing -Domain $liveDomain -FileType 'pdf' -UserAgent $liveAgent -MaxPages 1)
    Write-Host "[info] Bing returned $($links.Count) PDF links on $liveDomain"
    $links | Select-Object -First 5 | ForEach-Object { Write-Host "       $_" }

    Write-Host ""
    Write-Host "Live search (one DuckDuckGo query for PDF files on $liveDomain)"
    $ddgBlocked = $false
    $links = @(Search-PowerMetaDuckDuckGo -Domain $liveDomain -FileType 'pdf' -UserAgent $liveAgent -MaxPages 1 -Blocked ([ref]$ddgBlocked))
    Write-Host "[info] DuckDuckGo returned $($links.Count) PDF links on $liveDomain (bot check shown: $ddgBlocked)"
    $links | Select-Object -First 5 | ForEach-Object { Write-Host "       $_" }
}

Write-Host ""
if ($failures -eq 0) {
    Write-Host "All checks passed."
}
else {
    Write-Host "$failures check(s) FAILED."
    exit 1
}
