<#
.SYNOPSIS
    Self-test for justOSINT. Checks the helper functions without touching the network, and tests
    metadata extraction on two small PDF files it creates itself (if ExifTool is installed).

.PARAMETER Live
    Also run one real Bing search, one real DuckDuckGo search and one real Internet Archive lookup,
    to see what each of them returns from this computer.

.PARAMETER TargetDomain
    The domain to use for the -Live search.

.EXAMPLE
    .\Test-justOSINT.ps1

.EXAMPLE
    .\Test-justOSINT.ps1 -Live -TargetDomain targetdomain.com

.NOTES
    Keep this file plain ASCII: Windows PowerShell 5.1 misreads other characters in script files.
#>

param(
    [switch]$Live,

    [string]$TargetDomain = "microsoft.com"
)

. (Join-Path $PSScriptRoot 'justOSINT.ps1')

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

Assert-Equal 'Bing link is unwrapped' (ConvertFrom-JustOSINTBingLink 'https://www.bing.com/ck/a?!&&p=abc123&ptn=3&ver=2&hsh=4&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9hL3JlcG9ydC5wZGY&ntb=1') 'https://example.com/a/report.pdf'
Assert-Equal 'Bing link with a query string is unwrapped' (ConvertFrom-JustOSINTBingLink 'https://www.bing.com/ck/a?!&&p=abc123&u=a1aHR0cHM6Ly93d3cuZXhhbXBsZS5jb20vZG9jcy9Bbm51YWwlMjBSZXBvcnQuZG9jeD9kb3dubG9hZD0xJnY9Mg&ntb=1') 'https://www.example.com/docs/Annual%20Report.docx?download=1&v=2'
Assert-Equal 'Bing link needing padding is unwrapped' (ConvertFrom-JustOSINTBingLink 'https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9hYQ') 'https://example.com/aa'
Assert-Equal 'A link that is not wrapped is left alone' (ConvertFrom-JustOSINTBingLink 'https://example.com/a/report.pdf') 'https://example.com/a/report.pdf'
Assert-Equal 'A wrapped link without an address gives nothing' (ConvertFrom-JustOSINTBingLink 'https://www.bing.com/ck/a?!&&p=abc123') ''

Assert-Equal 'Bing link to one of its own pages gives nothing' (ConvertFrom-JustOSINTBingLink 'https://www.bing.com/ck/a?u=a1L2ltYWdlcy9zZWFyY2g_cT14') ''

Assert-Equal 'DuckDuckGo link is unwrapped' (ConvertFrom-JustOSINTDuckDuckGoLink '//duckduckgo.com/l/?uddg=https%3A%2F%2Ffiles.example.com%2Fa%2Fbudget%2D2025.pdf%3Fdl%3D1&rut=0123abcd') 'https://files.example.com/a/budget-2025.pdf?dl=1'
Assert-Equal 'A plain DuckDuckGo result is left alone' (ConvertFrom-JustOSINTDuckDuckGoLink 'https://www.example.com/docs/Annual%20Report.pdf') 'https://www.example.com/docs/Annual%20Report.pdf'

$ddgPage = '<div class="nav-link"><form action="/html/" method="post"><input type="submit" value="Previous" /><input type="hidden" name="q" value="site:example.com filetype:pdf" /><input type="hidden" name="s" value="0" /></form></div><div class="nav-link"><form action="/html/" method="post"><input type="submit" value="Next" /><input type="hidden" name="q" value="site:example.com filetype:pdf" /><input type="hidden" name="s" value="60" /><input type="hidden" name="vqd" value="4-222&amp;x" /></form></div>'
$nextForm = Get-JustOSINTDuckDuckGoNextForm -Html $ddgPage -After 30
$nextOffset = ''
$nextToken = ''
if ($nextForm) {
    $nextOffset = $nextForm['s']
    $nextToken = $nextForm['vqd']
}
Assert-Equal 'DuckDuckGo "Next" form is found' $nextOffset '60'
Assert-Equal 'Its fields are decoded' $nextToken '4-222&x'
Assert-Equal 'There is no "Next" form on the last page' (Get-JustOSINTDuckDuckGoNextForm -Html $ddgPage -After 60) ''

$archiveAnswer = "http://www.example.com:80/docs/Report.pdf 20190102030405`nhttps://files.example.com/a/budget.xlsx?v=2 20210607080910`nhttps://www.example.com/about.html 20200101000000`nhttps://www.example.com/docs/Report.pdf 20220102030405`nhttps://other.org/x.pdf 20200101000000`nnot an index line`n"
$archiveEntries = @(ConvertFrom-JustOSINTArchiveIndex -Text $archiveAnswer -Domain 'example.com' -FileTypes @('pdf', 'xlsx'))
Assert-Equal 'Internet Archive answer: files on the domain are kept, the rest dropped' (($archiveEntries | ForEach-Object { $_.Link }) -join '|') 'http://www.example.com/docs/Report.pdf|https://files.example.com/a/budget.xlsx?v=2|https://www.example.com/docs/Report.pdf'
$firstCopy = ''
if ($archiveEntries.Count -gt 0) { $firstCopy = $archiveEntries[0].ArchiveCopy }
Assert-Equal 'Internet Archive answer: the address of the archived copy is built' $firstCopy 'https://web.archive.org/web/20190102030405id_/http://www.example.com:80/docs/Report.pdf'

Assert-Equal 'The http and https addresses of a file compare as equal' (Get-JustOSINTLinkKey 'http://WWW.Example.com:80/docs/Report.pdf?v=1') (Get-JustOSINTLinkKey 'https://www.example.com/docs/Report.pdf?v=1')
Assert-Equal 'An unusual port stays part of the comparison' (Get-JustOSINTLinkKey 'https://www.example.com:8443/a.pdf') 'www.example.com:8443/a.pdf'

Assert-Equal 'Links are read from HTML' ((@(Get-JustOSINTLinksFromHtml '<p><a class="x" href="https://example.com/a.pdf?x=1&amp;y=2">one</a> <A HREF=''/relative''>two</A> <a href=bare.html>three</a></p>')) -join '|') 'https://example.com/a.pdf?x=1&y=2|/relative|bare.html'

Assert-Equal 'File on the domain is accepted' (Test-JustOSINTFileLink -Link 'https://example.com/x/report.pdf' -Domain 'example.com' -FileType 'pdf') $true
Assert-Equal 'File on a sub-domain is accepted' (Test-JustOSINTFileLink -Link 'http://files.example.com/x/REPORT.PDF?dl=1' -Domain 'example.com' -FileType 'pdf') $true
Assert-Equal 'File named in the query string is accepted' (Test-JustOSINTFileLink -Link 'https://example.com/get.php?f=a.pdf&t=1' -Domain 'example.com' -FileType 'pdf') $true
Assert-Equal 'docx is not reported as doc' (Test-JustOSINTFileLink -Link 'https://example.com/a.docx' -Domain 'example.com' -FileType 'doc') $false
Assert-Equal 'Other domain is rejected' (Test-JustOSINTFileLink -Link 'https://notexample.com/a.pdf' -Domain 'example.com' -FileType 'pdf') $false
Assert-Equal 'Look-alike domain is rejected' (Test-JustOSINTFileLink -Link 'https://example.com.evil.org/a.pdf' -Domain 'example.com' -FileType 'pdf') $false
Assert-Equal 'Relative link is rejected' (Test-JustOSINTFileLink -Link '/search?q=site%3aexample.com+filetype%3apdf' -Domain 'example.com' -FileType 'pdf') $false
Assert-Equal 'Page without the extension is rejected' (Test-JustOSINTFileLink -Link 'https://example.com/pdf/list' -Domain 'example.com' -FileType 'pdf') $false

Write-Host ""
Write-Host "Input handling"

Assert-Equal 'File types are split and cleaned' ((@(Get-JustOSINTFileTypeList 'pdf, .DOCX,xlsx,,pdf')) -join '|') 'pdf|docx|xlsx'
Assert-Equal 'Domain is taken from a URL' (Get-JustOSINTHostName ' HTTPS://www.TargetDomain.com/path?q=1 ') 'www.targetdomain.com'
Assert-Equal 'Wildcard domain is accepted' (Get-JustOSINTHostName '*.targetdomain.com') 'targetdomain.com'

Write-Host ""
Write-Host "File names for downloads"

Assert-Equal 'Plain name is kept' (Get-JustOSINTFileName 'https://example.com/a/report.pdf') 'report.pdf'
Assert-Equal 'Query string is dropped' (Get-JustOSINTFileName 'https://example.com/a/report.pdf?x=1') 'report.pdf'
Assert-Equal 'Missing name becomes download' (Get-JustOSINTFileName 'https://example.com/a/') 'download'
Assert-Equal 'PowerShell syntax in a name is defused' (Get-JustOSINTFileName 'https://example.com/x/$(calc);.pdf') '_(calc)_.pdf'
Assert-Equal 'Reserved device name is changed' (Get-JustOSINTFileName 'https://example.com/con.pdf') '_con.pdf'
Assert-Equal 'A download cannot be named like ExifTool' (Get-JustOSINTFileName 'https://example.com/a.pdf/exiftool.exe') '_exiftool.exe.pdf'
Assert-Equal 'A download cannot be named like the ExifTool configuration file' (Get-JustOSINTFileName 'https://example.com/x/.ExifTool_config') '_.ExifTool_config.download'
Assert-Equal 'The site does not choose the ending: a program' (Get-JustOSINTFileName 'https://example.com/files/setup.exe?doc=.pdf') 'setup.exe.pdf'
Assert-Equal 'The site does not choose the ending: a script' (Get-JustOSINTFileName 'https://example.com/files/run.bat?x.pdf') 'run.bat.pdf'
Assert-Equal 'The site does not choose the ending: a shortcut' (Get-JustOSINTFileName 'https://example.com/files/report.pdf.lnk') 'report.pdf.lnk.pdf'
Assert-Equal 'A file named in the query string gets its type' (Get-JustOSINTFileName 'https://example.com/get.php?file=annual.docx&x=1') 'get.php.docx'
Assert-Equal 'A name without a wanted type is marked' (Get-JustOSINTFileName 'https://example.com/files/tool.ps1') 'tool.ps1.download'
Assert-Equal 'A name without any ending is kept' (Get-JustOSINTFileName 'https://example.com/files/readme') 'readme'
Assert-Equal 'An upper-case ending is a wanted one too' (Get-JustOSINTFileName 'https://example.com/files/REPORT.PDF') 'REPORT.PDF'
Assert-Equal 'The wanted types are those that were asked for' (Get-JustOSINTFileName -Link 'https://example.com/a.pdf?as=b.odt' -FileTypes @('odt')) 'a.pdf.odt'
Assert-Equal 'A download cannot look like a command-line option' (Get-JustOSINTFileName 'https://example.com/x/-o.pdf') '_-o.pdf'

# Cells a spreadsheet program would run as a formula
$csvIn = (@('SourceFile,Author,Comment', 'a.pdf,=1+1,"@SUM(A1), ""quoted"""', 'b.pdf,-5,"two', 'lines, one cell"', 'c.pdf,+0.3,-1+cmd') -join "`n")
$csvOut = (@('SourceFile,Author,Comment', "a.pdf,'=1+1,`"'@SUM(A1), `"`"quoted`"`"`"", 'b.pdf,-5,"two', 'lines, one cell"', "c.pdf,+0.3,'-1+cmd") -join "`n")
Assert-Equal 'Formula cells in the CSV are made plain text, numbers and other cells are kept' (ConvertTo-JustOSINTInertCsv -Text $csvIn) $csvOut
Assert-Equal 'An empty CSV stays empty' (ConvertTo-JustOSINTInertCsv -Text '') ''
Assert-Equal 'A formula behind a semicolon inside a cell is made plain text' (ConvertTo-JustOSINTInertCsv -Text 'a.pdf,x;=1+1,Writer') "a.pdf,x;'=1+1,Writer"
Assert-Equal 'A formula on the second line of a cell is made plain text' (ConvertTo-JustOSINTInertCsv -Text ("a.pdf,`"x`n=1+1`",Writer")) ("a.pdf,`"x`n'=1+1`",Writer")
Assert-Equal 'A list with semicolons is left alone' (ConvertTo-JustOSINTInertCsv -Text 'a.pdf,one;two;3,Writer') 'a.pdf,one;two;3,Writer'

Write-Host ""
Write-Host "Metadata extraction"

$exiftool = Get-JustOSINTExifTool
if (-not $exiftool) {
    Write-Host "[skip] ExifTool is not installed, so metadata extraction was not tested. Run Install-ExifTool, then run this again."
}
elseif (-not (Test-JustOSINTExifTool $exiftool)) {
    Write-Host "[skip] The ExifTool that was found cannot be used (see the message above)."
}
else {
    Write-Host "[info] Using $exiftool"
    $folder = Join-Path ([System.IO.Path]::GetTempPath()) ('justosint-selftest-' + [System.Guid]::NewGuid().ToString('N'))
    $files = Join-Path $folder 'files with space'
    New-Item -ItemType Directory -Path $files -Force | Out-Null
    try {
        New-TestPdf -Path (Join-Path $files 'one.pdf') -Author 'jsmith' -Creator 'Writer, version 1'
        # A file name that the old Invoke-Expression code would have run as a command
        New-TestPdf -Path (Join-Path $files 'two$(x);.pdf') -Author 'adoe' -Creator 'Writer, version 1'

        $output = @(ExtractMetadata -OutputDir $files)
        Assert-Equal 'Both authors are found' (($output -contains '    jsmith') -and ($output -contains '    adoe')) $true
        Assert-Equal 'A value containing a comma stays in one piece' ($output -contains '    Writer, version 1') $true

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
    $liveDomain = Get-JustOSINTHostName $TargetDomain
    # The same agent string Invoke-JustOSINT sends by default
    $liveAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"

    Write-Host ""
    Write-Host "Live search (one Bing query for PDF files on $liveDomain)"
    $links = @(Search-JustOSINTBing -Domain $liveDomain -FileType 'pdf' -UserAgent $liveAgent -MaxPages 1)
    Write-Host "[info] Bing returned $($links.Count) PDF links on $liveDomain"
    $links | Select-Object -First 5 | ForEach-Object { Write-Host "       $_" }

    Write-Host ""
    Write-Host "Live search (one DuckDuckGo query for PDF files on $liveDomain)"
    $ddgBlocked = $false
    $links = @(Search-JustOSINTDuckDuckGo -Domain $liveDomain -FileType 'pdf' -UserAgent $liveAgent -MaxPages 1 -Blocked ([ref]$ddgBlocked))
    Write-Host "[info] DuckDuckGo returned $($links.Count) PDF links on $liveDomain (bot check shown: $ddgBlocked)"
    $links | Select-Object -First 5 | ForEach-Object { Write-Host "       $_" }

    Write-Host ""
    Write-Host "Live lookup (the Internet Archive's index, all default file types on $liveDomain)"
    $entries = @(Search-JustOSINTArchive -Domain $liveDomain -FileTypes @('pdf', 'docx', 'xlsx', 'doc', 'xls', 'pptx', 'ppt') -UserAgent $liveAgent)
    Write-Host "[info] The Internet Archive listed $($entries.Count) files on $liveDomain"
    $entries | Select-Object -First 5 | ForEach-Object { Write-Host "       $($_.Link)" }
}

Write-Host ""
if ($failures -eq 0) {
    Write-Host "All checks passed."
}
else {
    Write-Host "$failures check(s) FAILED."
    exit 1
}
