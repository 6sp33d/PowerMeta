# PowerMeta
# Finds documents that a domain has published, downloads them, and reads the metadata
# (author, creator, ...) out of them with ExifTool.
# Original author: Beau Bullock (@dafthack)
#
# Keep this file plain ASCII: Windows PowerShell 5.1 misreads other characters in script files.

# TLS: Windows PowerShell on an old .NET Framework offers only SSL3/TLS 1.0 by default, which
# search engines no longer accept, so TLS 1.2 is added in that case. A value of 0 means
# "SystemDefault" (Windows already negotiates TLS 1.2/1.3) and is left alone.
& {
    try {
        $current = [int][System.Net.ServicePointManager]::SecurityProtocol
        $tls12 = [int][System.Net.SecurityProtocolType]::Tls12
        if ($current -ne 0 -and ($current -band $tls12) -eq 0) {
            [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]($current -bor $tls12)
        }
    } catch { }
}

Function Invoke-PowerMeta{

    <#
    .SYNOPSIS
    This function searches for publicly available files hosted on a particular domain by using search engine queries such as "site:targetdomain.com filetype:pdf". It then allows for the download of those files from the target domain. After retrieving the files, the metadata associated with them can be analyzed by PowerMeta. Some interesting things commonly found in metadata are usernames, domains, software titles, and computer names.

    PowerMeta Function: Invoke-PowerMeta
    Author: Beau Bullock (@dafthack)
    License: BSD 3-Clause
    Required Dependencies: None
    Optional Dependencies: ExifTool 12.24 or later for metadata extraction (see Install-ExifTool)

    .DESCRIPTION
    This function searches for publicly available files hosted on a particular domain by using search engine queries such as "site:targetdomain.com filetype:pdf". It then allows for the download of those files from the target domain. After retrieving the files, the metadata associated with them can be analyzed by PowerMeta. Some interesting things commonly found in metadata are usernames, domains, software titles, and computer names.

    .PARAMETER TargetDomain
    The target domain to search for files.

    .PARAMETER FileTypes
    A comma seperated list of file extensions to search for. By default PowerMeta searches for "pdf, docx, xlsx, doc, xls, pptx, ppt".

    .PARAMETER OutputList
    A file to output the list of links discovered through web searching to.

    .PARAMETER OutputDir
    A directory to store all downloaded files in.

    .PARAMETER TargetFileList
    List of file links to download. When this is given no searching is done.

    .PARAMETER Download
    Instead of being prompted interactively pass this flag to auto-download files found.

    .PARAMETER Extract
    Instead of being prompted interactively pass this flag to extract metadata from found files pass this flag to auto-extract any metadata.

    .PARAMETER ExtractAllToCsv
    All metadata (not just the default fields) will be extracted from files to a CSV specified with this flag.

    .PARAMETER UserAgent
    Change the default User Agent used by PowerMeta.

    .PARAMETER MaxSearchPages
    The maximum number of pages to search on each search engine.

    .PARAMETER SearchEngines
    The sources to ask: Bing, DuckDuckGo, Archive (the Internet Archive's index of the addresses it has seen on the domain), or any combination. All three by default. Example: -SearchEngines Archive

    .PARAMETER SearchDelay
    Seconds to wait between two requests to the same search engine. Default 2.

    .Example

    C:\PS> Invoke-PowerMeta -TargetDomain targetdomain.com
    Description
    -----------
    This command will search for files on the 'targetdomain.com' domain ending with a file extension of pdf, docx, xlsx, doc, xls, pptx, or ppt. Once it has finished crafting this list it will prompt the user asking if they wish to download the files from the target domain. After downloading files it will prompt again for extraction of metadata from those files.

    .Example

    C:\PS> Invoke-PowerMeta -TargetDomain targetdomain.com -FileTypes "pdf, xml" -Download -Extract
    Description
    -----------
    This command will search for files on the 'targetdomain.com' domain ending with a file extension of pdf, or xml. It will then automatically download them from the target domain and extract metadata.

    .Example

    C:\PS> Invoke-PowerMeta -TargetDomain targetdomain.com -OutputList target-domain-links.txt
    Description
    -----------
    This command will search for files on the 'targetdomain.com' domain and write the links of the files found to disk in a file called "target-domain-links.txt".

    .Example

    C:\PS> Invoke-PowerMeta -TargetDomain targetdomain.com -TargetFileList target-domain-links.txt
    Description
    -----------
    This command skips searching and uses the links listed in "target-domain-links.txt" (one per line) instead.

    .Example

    C:\PS> Invoke-PowerMeta -TargetDomain targetdomain.com -MaxSearchPages 2 -ExtractAllToCsv all-target-metadata.csv
    Description
    -----------
    This command will search for files on the 'targetdomain.com' domain but only search the first two pages of results. All metadata (not just the default fields) will be saved in a CSV called all-target-metadata.csv.

    .Example

    C:\PS> ExtractMetadata -OutputDir .\2017-03-031-144953\ -ExtractAllToCsv all-target-metadata.csv
    Description
    -----------
    This command will simply extract all the metadata from all the files in the folder "\2017-03-031-144953\" and save it in a CSV called all-target-metadata.csv

    #>

      Param
      (
        [Parameter(Position = 0, Mandatory = $true)]
        [string]
        $TargetDomain = "",

        [Parameter(Position = 1, Mandatory = $false)]
        [string]
        $FileTypes = "pdf, docx, xlsx, doc, xls, pptx, ppt",

        [Parameter(Position = 2, Mandatory = $false)]
        [string]
        $OutputList = "",

        [Parameter(Position = 3, Mandatory = $false)]
        [string]
        $OutputDir = "",

        [Parameter(Position = 4, Mandatory = $false)]
        [string]
        $TargetFileList = "",

        [Parameter(Position = 5, Mandatory = $false)]
        [switch]
        $Download,

        [Parameter(Position = 6, Mandatory = $false)]
        [switch]
        $Extract,

        [Parameter(Position = 7, Mandatory = $false)]
        [string]
        $ExtractAllToCsv = "",

        [Parameter(Position = 8, Mandatory = $false)]
        [string]
        $UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",

        [Parameter(Position = 9, Mandatory = $false)]
        [int]
        $MaxSearchPages = 5,

        [Parameter(Mandatory = $false)]
        [ValidateSet('Bing', 'DuckDuckGo', 'Archive')]
        [string[]]
        $SearchEngines = @('Bing', 'DuckDuckGo', 'Archive'),

        [Parameter(Mandatory = $false)]
        [int]
        $SearchDelay = 2

        )

    $targetlinks = @()

    #For links found through the Internet Archive: where the Archive keeps its copy of the file.
    #A plain .NET Hashtable, because its keys are case-sensitive (links can differ only in letter case).
    $archiveCopies = New-Object System.Collections.Hashtable

    #If no target file list was provided we will perform searches and craft one.
    $filetypearray = @(Get-PowerMetaFileTypeList $FileTypes)

    If ($TargetFileList -eq "")
        {
            $domain = Get-PowerMetaHostName $TargetDomain
            if ($domain -notmatch '^[\p{L}\p{N}]([\p{L}\p{N}.\-]*[\p{L}\p{N}])?$')
                {
                Write-Output "[!] '$TargetDomain' is not a domain name. Use a name such as targetdomain.com."
                return
                }

            if ($filetypearray.Count -eq 0)
                {
                Write-Output "[!] No file types to search for. Pass a comma separated list such as `"pdf, docx`"."
                return
                }

            $validlinks = New-Object System.Collections.Generic.List[string]

            #Links are compared without "http://" or "https://": the Internet Archive often knows a file under
            #its old http address while a search engine gives today's https one
            $linkByKey = New-Object System.Collections.Hashtable

            #The Internet Archive answers for all file types in a single request
            $archiveEntries = @()
            if ($SearchEngines -contains 'Archive')
                {
                $archiveEntries = @(Search-PowerMetaArchive -Domain $domain -FileTypes $filetypearray -UserAgent $UserAgent)
                }

            #The search engines are asked once per file type. DuckDuckGo is left alone for the rest of the
            #run once it has refused to answer.
            $duckDuckGoBlocked = $false
            $firstFileType = $true
            foreach ($filetype in $filetypearray)
                {
                $askBing = ($SearchEngines -contains 'Bing')
                $askDuckDuckGo = ($SearchEngines -contains 'DuckDuckGo' -and -not $duckDuckGoBlocked)
                if (-not $askBing -and -not $askDuckDuckGo) { break }

                #The pause also applies between the last request for one file type and the first for the next
                if (-not $firstFileType -and $SearchDelay -gt 0) { Start-Sleep -Seconds $SearchDelay }
                $firstFileType = $false

                $found = @()

                if ($askBing)
                    {
                    $found += @(Search-PowerMetaBing -Domain $domain -FileType $filetype -UserAgent $UserAgent -MaxPages $MaxSearchPages -DelaySeconds $SearchDelay)
                    }

                if ($askDuckDuckGo)
                    {
                    $found += @(Search-PowerMetaDuckDuckGo -Domain $domain -FileType $filetype -UserAgent $UserAgent -MaxPages $MaxSearchPages -DelaySeconds $SearchDelay -Blocked ([ref]$duckDuckGoBlocked))
                    }

                foreach ($link in $found)
                    {
                    $key = Get-PowerMetaLinkKey $link
                    if (-not $linkByKey.ContainsKey($key))
                        {
                        $linkByKey[$key] = $link
                        $validlinks.Add($link)
                        }
                    }
                }

            #The Archive's files are added last, so that for a file both know the search engine's current
            #address is the one kept. Either way the Archive's copy is remembered for it.
            foreach ($entry in $archiveEntries)
                {
                $key = Get-PowerMetaLinkKey $entry.Link
                if (-not $linkByKey.ContainsKey($key))
                    {
                    $linkByKey[$key] = $entry.Link
                    $validlinks.Add($entry.Link)
                    }
                $kept = [string]$linkByKey[$key]
                if (-not $archiveCopies.ContainsKey($kept)) { $archiveCopies[$kept] = $entry.ArchiveCopy }
                }

            $targetlinks = @($validlinks | Sort-Object)

            Write-Output ('[*] A total of ' + $targetlinks.Count + ' files were discovered.')
            Write-Output "`n"

            #If an OutputList is specified write the results to disk
            If ($OutputList -ne "" -and $targetlinks.Count -gt 0)
                {
                Write-Output "[*] Writing links to a file at $OutputList"
                $targetlinks | Out-File -LiteralPath $OutputList -Encoding ascii
                }

        }
    else
        {
        #If a target list was specified let's use that.
        if (-not (Test-Path -LiteralPath $TargetFileList -PathType Leaf))
            {
            Write-Output "[!] The file list $TargetFileList does not exist."
            return
            }
        $targetlinks = @(Get-Content -LiteralPath $TargetFileList | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne '' })
        Write-Output "[*] Using the file list at $TargetFileList"
        Write-Output ('[*] A total of ' + $targetlinks.Count + ' files were found.')
        }

    if ($targetlinks.Count -eq 0)
        {
        Write-Output "[*] Nothing to download."
        return
        }

    # If no output directory was named we will create one in with the current date/time.
    If($OutputDir -eq "")
        {
            $OutputDir = Get-Date -format yyyy-MM-dd-HHmmss
        }

    If ($Download)
    {
        Get-TargetFiles -targetlinks $targetlinks -OutputDir $OutputDir -UserAgent $UserAgent -ArchiveCopies $archiveCopies -FileTypes $filetypearray
    }
    else
        {
            $title = "Download Files?"
            $message = "Would you like to download all of the files discovered?"

            $yes = New-Object System.Management.Automation.Host.ChoiceDescription "&Yes", "Downloads all of the target files."
            $no = New-Object System.Management.Automation.Host.ChoiceDescription "&No", "The files will not be downloaded."

            $options = [System.Management.Automation.Host.ChoiceDescription[]]($yes, $no)

            $result = $host.ui.PromptForChoice($title, $message, $options, 0)

            switch ($result)
                {
                 0 {
                    "[*] Now downloading the files."
                    Get-TargetFiles -targetlinks $targetlinks -OutputDir $OutputDir -UserAgent $UserAgent -ArchiveCopies $archiveCopies -FileTypes $filetypearray
                   }
                 1 {"[*] No files will be downloaded."}
                }

        }

    # Without a folder of files there is nothing to extract metadata from.
    if (-not (Test-Path -LiteralPath $OutputDir -PathType Container))
        {
        return
        }

    If ($Extract)
        {
            Write-Output "[*] Now extracting metadata from the files."
            ExtractMetadata -OutputDir $OutputDir -ExtractAllToCsv $ExtractAllToCsv
        }
    else
        {
            $title = "Extract Metadata?"
            $message = "Would you like to extract metadata from all of the files downloaded now?"

            $yes = New-Object System.Management.Automation.Host.ChoiceDescription "&Yes", "Extracts metadata from downloaded files."
            $no = New-Object System.Management.Automation.Host.ChoiceDescription "&No", "No metadata will be extracted at this time."

            $options = [System.Management.Automation.Host.ChoiceDescription[]]($yes, $no)

            $result = $host.ui.PromptForChoice($title, $message, $options, 0)

            switch ($result)
                {
                 0 {
                    Write-Output "[*] Now extracting metadata from the files."
                    ExtractMetadata -OutputDir $OutputDir -ExtractAllToCsv $ExtractAllToCsv
                   }
                 1 {"[*] No metadata will be extracted at this time. If you wish to extract metadata later you can run the ExtractMetadata function on a directory of files like so: PS C:>ExtractMetadata -OutputDir `"C:\Users\username\directory-of-files\`""}
                }
        }
}

#Splits "pdf, docx,xlsx" into clean lower-case extensions
function Get-PowerMetaFileTypeList {
    param([string]$FileTypes)

    $types = @()
    foreach ($entry in ($FileTypes -split ','))
        {
        $clean = $entry.Trim().TrimStart('.').ToLowerInvariant()
        if ($clean -eq '') { continue }
        if ($clean -notmatch '^[a-z0-9]{1,12}$')
            {
            Write-Warning "Ignoring file type '$($entry.Trim())': use letters and digits only, for example pdf."
            continue
            }
        if ($types -notcontains $clean) { $types += $clean }
        }
    $types
}

#Reduces "https://www.TargetDomain.com/path" or "*.targetdomain.com" to a bare host name
function Get-PowerMetaHostName {
    param([string]$TargetDomain)

    $name = $TargetDomain.Trim().ToLowerInvariant()
    $name = $name -replace '^[a-z][a-z0-9+.\-]*://', ''
    $name = $name -replace '[/?#].*$', ''
    $name = $name -replace '^.*@', ''
    $name = $name -replace ':\d+$', ''
    $name = $name -replace '^\*\.', ''
    $name.Trim('.')
}

#Fetches a page and returns its text, or nothing if the request failed.
#With -Method Post the hashtable given as -Body is sent as form fields.
function Get-PowerMetaWebPage {
    param(
        [string]$Url,
        [string]$UserAgent = "",
        $Session = $null,
        [int]$TimeoutSec = 60,
        [string]$Method = 'Get',
        [hashtable]$Body = $null
    )

    $ProgressPreference = 'SilentlyContinue'
    $request = @{ Uri = $Url; Method = $Method; UseBasicParsing = $true; TimeoutSec = $TimeoutSec; ErrorAction = 'Stop' }
    if ($UserAgent -ne "") { $request['UserAgent'] = $UserAgent }
    if ($null -ne $Session) { $request['WebSession'] = $Session }
    if ($null -ne $Body) { $request['Body'] = $Body }

    try
        {
        $response = Invoke-WebRequest @request
        $content = $response.Content
        if ($content -is [byte[]]) { $content = [System.Text.Encoding]::UTF8.GetString($content) }
        return [string]$content
        }
    catch
        {
        Write-Host "[!] Request to $Url failed: $($_.Exception.Message)"
        return $null
        }
}

#Returns the href of every link in a page, with HTML entities such as &amp; decoded
function Get-PowerMetaLinksFromHtml {
    param([string]$Html)

    $links = New-Object System.Collections.Generic.List[string]
    if ($Html)
        {
        $pattern = '<a\s[^>]*?href\s*=\s*(?:"([^"]*)"|''([^'']*)''|([^\s>]+))'
        foreach ($match in [regex]::Matches($Html, $pattern, 'IgnoreCase'))
            {
            $value = $match.Groups[1].Value + $match.Groups[2].Value + $match.Groups[3].Value
            if ($value -ne '') { $links.Add([System.Net.WebUtility]::HtmlDecode($value)) }
            }
        }
    $links
}

#Bing wraps each result as https://www.bing.com/ck/a?...&u=a1<base64url of the real address>&ntb=1
#(two marker characters, then the address). This returns the real address, the link unchanged if it
#is not wrapped, or nothing if it cannot be decoded into an http(s) address.
function ConvertFrom-PowerMetaBingLink {
    param([string]$Link)

    if ($Link -notmatch '^(?:https?:)?//(?:www\.)?bing\.com/ck/a\?') { return $Link }

    $match = [regex]::Match($Link, '[?&]u=[A-Za-z0-9]{2}([A-Za-z0-9_\-]+)')
    if (-not $match.Success) { return $null }

    $encoded = $match.Groups[1].Value.Replace('-', '+').Replace('_', '/')
    switch ($encoded.Length % 4)
        {
        2 { $encoded += '==' }
        3 { $encoded += '=' }
        }

    try
        {
        $decoded = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($encoded))
        }
    catch
        {
        return $null
        }

    if ($decoded -notmatch '^https?://') { return $null }
    return $decoded
}

#DuckDuckGo gives results either as plain addresses or wrapped as //duckduckgo.com/l/?uddg=<url-encoded address>&rut=...
#This returns the real address, the link unchanged if it is not wrapped, or nothing if it cannot be decoded.
function ConvertFrom-PowerMetaDuckDuckGoLink {
    param([string]$Link)

    if ($Link -notmatch '^(?:https?:)?//(?:www\.)?duckduckgo\.com/l/\?') { return $Link }

    $match = [regex]::Match($Link, '[?&]uddg=([^&#]+)')
    if (-not $match.Success) { return $null }

    $decoded = [System.Uri]::UnescapeDataString($match.Groups[1].Value)
    if ($decoded -notmatch '^https?://') { return $null }

    #Keep the address in plain ASCII, the way search engines normally give it, so that it survives being written to a list
    if ($decoded -match '[^\x20-\x7E]')
        {
        $uri = $null
        if ([System.Uri]::TryCreate($decoded, [System.UriKind]::Absolute, [ref]$uri)) { $decoded = $uri.AbsoluteUri }
        }
    return $decoded
}

#Returns the fields of the "Next" form on a DuckDuckGo results page as a hashtable, or nothing on the last page.
#The form carries the query (q), the offset of the next page (s) and the tokens DuckDuckGo wants sent back.
function Get-PowerMetaDuckDuckGoNextForm {
    param([string]$Html, [int]$After = 0)

    $best = $null
    $bestOffset = $After
    foreach ($form in [regex]::Matches($Html, '<form\b[^>]*>(.*?)</form>', 'IgnoreCase, Singleline'))
        {
        $fields = @{}
        foreach ($field in [regex]::Matches($form.Groups[1].Value, '<input\b[^>]*>', 'IgnoreCase'))
            {
            $nameMatch = [regex]::Match($field.Value, '\bname\s*=\s*(?:"([^"]*)"|''([^'']*)'')', 'IgnoreCase')
            if (-not $nameMatch.Success) { continue }
            $fieldName = $nameMatch.Groups[1].Value + $nameMatch.Groups[2].Value
            if ($fieldName -eq '') { continue }

            $fieldValue = ''
            $valueMatch = [regex]::Match($field.Value, '\bvalue\s*=\s*(?:"([^"]*)"|''([^'']*)'')', 'IgnoreCase')
            if ($valueMatch.Success)
                {
                $fieldValue = [System.Net.WebUtility]::HtmlDecode($valueMatch.Groups[1].Value + $valueMatch.Groups[2].Value)
                }
            $fields[$fieldName] = $fieldValue
            }

        #The "Next" form is the one whose offset lies beyond the page just read ("Previous" points backwards)
        $offset = 0
        if ($fields.ContainsKey('q') -and $fields.ContainsKey('s') -and [int]::TryParse([string]$fields['s'], [ref]$offset) -and $offset -gt $bestOffset)
            {
            $best = $fields
            $bestOffset = $offset
            }
        }
    $best
}

#True if the link is an http(s) address on the domain (or one of its sub-domains)
function Test-PowerMetaDomainLink {
    param([string]$Link, [string]$Domain)

    $uri = $null
    if (-not [System.Uri]::TryCreate($Link, [System.UriKind]::Absolute, [ref]$uri)) { return $false }
    if ($uri.Scheme -ne 'http' -and $uri.Scheme -ne 'https') { return $false }

    $name = $uri.Host.ToLowerInvariant()
    $wanted = $Domain.ToLowerInvariant()
    return ($name -eq $wanted -or $name.EndsWith('.' + $wanted, [System.StringComparison]::Ordinal))
}

#True if the link is on the domain and names a file of the wanted type.
#".doc" must not be followed by another letter or digit, so a search for doc does not pick up docx.
function Test-PowerMetaFileLink {
    param([string]$Link, [string]$Domain, [string]$FileType)

    if (-not (Test-PowerMetaDomainLink -Link $Link -Domain $Domain)) { return $false }

    $uri = New-Object System.Uri($Link)
    $pattern = '\.' + [regex]::Escape($FileType) + '(?![A-Za-z0-9])'
    return [regex]::IsMatch($uri.PathAndQuery, $pattern, 'IgnoreCase')
}

#Searches Bing for "site:domain filetype:type" and returns the matching file links
function Search-PowerMetaBing {
    param(
        [string]$Domain,
        [string]$FileType,
        [string]$UserAgent = "",
        [int]$MaxPages = 5,
        [int]$DelaySeconds = 2
    )

    $links = New-Object System.Collections.Generic.List[string]
    $query = [System.Uri]::EscapeDataString("site:$Domain filetype:$FileType")
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $first = 1
    $resultsSeen = 0
    $onDomainSeen = 0

    Write-Host "[*] Searching Bing for 'site:$Domain filetype:$FileType'"

    for ($page = 1; $page -le $MaxPages; $page++)
        {
        if ($page -gt 1 -and $DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }

        Write-Host "[*] Now Analyzing page $page of Bing search results"
        $html = Get-PowerMetaWebPage -Url "https://www.bing.com/search?q=$query&first=$first" -UserAgent $UserAgent -Session $session
        if (-not $html) { break }

        #Each organic result sits in an element with class "b_algo"
        $results = ([regex]::Matches($html, 'class="b_algo[ "]')).Count
        $new = 0
        foreach ($href in @(Get-PowerMetaLinksFromHtml $html))
            {
            $link = ConvertFrom-PowerMetaBingLink $href
            if (-not $link) { continue }
            if (Test-PowerMetaDomainLink -Link $link -Domain $Domain) { $onDomainSeen++ }
            if (Test-PowerMetaFileLink -Link $link -Domain $Domain -FileType $FileType)
                {
                if (-not $links.Contains($link))
                    {
                    $links.Add($link)
                    $new++
                    }
                }
            }

        $resultsSeen += $results

        #No results, or nothing new: this was the last page
        if ($results -eq 0 -or $new -eq 0) { break }
        $first += $results
        }

    #Bing fills the page with other sites when it has little or nothing on the site for a query, and it has
    #also been seen doing so for automated queries. The two cases cannot be told apart from one answer.
    if ($resultsSeen -gt 0 -and $onDomainSeen -eq 0)
        {
        Write-Host "[!] Bing returned results, but none of them are on $Domain. It does this when it has little or nothing on the site for a search, and sometimes for automated searches."
        }
    Write-Host "[*] Bing: $($links.Count) $FileType files found"

    $links
}

#Searches DuckDuckGo's script-free page (html.duckduckgo.com) for "site:domain filetype:type" and returns the matching file links.
#DuckDuckGo wants the query as a form post, and gives each results page a "Next" form holding what to post for the following page.
#If it answers with its bot check instead of results, the [ref] passed as -Blocked is set to $true.
function Search-PowerMetaDuckDuckGo {
    param(
        [string]$Domain,
        [string]$FileType,
        [string]$UserAgent = "",
        [int]$MaxPages = 5,
        [int]$DelaySeconds = 2,
        [ref]$Blocked
    )

    $links = New-Object System.Collections.Generic.List[string]
    $query = "site:$Domain filetype:$FileType"
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $body = @{ q = $query; b = '' }
    $offset = 0
    $resultsSeen = 0
    $onDomainSeen = 0

    Write-Host "[*] Searching DuckDuckGo for '$query'"

    for ($page = 1; $page -le $MaxPages; $page++)
        {
        if ($page -gt 1 -and $DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }

        Write-Host "[*] Now Analyzing page $page of DuckDuckGo search results"
        $html = Get-PowerMetaWebPage -Url 'https://html.duckduckgo.com/html/' -Method 'Post' -Body $body -UserAgent $UserAgent -Session $session
        if (-not $html)
            {
            #No answer to the first request (an error status or a network failure): leave DuckDuckGo alone for the rest of the run
            if ($page -eq 1)
                {
                Write-Host "[!] DuckDuckGo did not answer the search. It is skipped for the rest of this run."
                if ($null -ne $Blocked) { $Blocked.Value = $true }
                }
            break
            }

        #Each result title is a link with class "result__a"
        $results = ([regex]::Matches($html, 'class="result__a"')).Count

        #The bot check ("Unfortunately, bots use DuckDuckGo too") comes instead of results
        if ($results -eq 0 -and $html -match 'anomaly-modal|challenge-form')
            {
            Write-Host "[!] DuckDuckGo answered with its bot check instead of results. It does this to automated requests from some networks; DuckDuckGo is skipped for the rest of this run. Try again later or from another network."
            if ($null -ne $Blocked) { $Blocked.Value = $true }
            break
            }

        $new = 0
        foreach ($href in @(Get-PowerMetaLinksFromHtml $html))
            {
            $link = ConvertFrom-PowerMetaDuckDuckGoLink $href
            if (-not $link) { continue }
            if (Test-PowerMetaDomainLink -Link $link -Domain $Domain) { $onDomainSeen++ }
            if (Test-PowerMetaFileLink -Link $link -Domain $Domain -FileType $FileType)
                {
                if (-not $links.Contains($link))
                    {
                    $links.Add($link)
                    $new++
                    }
                }
            }

        $resultsSeen += $results

        #Nothing new: this was the last page
        if ($new -eq 0) { break }

        $next = Get-PowerMetaDuckDuckGoNextForm -Html $html -After $offset
        if ($null -ne $next)
            {
            $body = $next
            $offset = [int]$next['s']
            }
        elseif ($results -ge 10 -and $html -notmatch 'name\s*=\s*["'']s["'']')
            {
            #A full page without a readable "Next" form: use the offsets DuckDuckGo's paging normally uses (10, 25, 40, ...)
            $offset = 10 + (($page - 1) * 15)
            $body = @{ q = $query; b = ''; s = [string]$offset }
            }
        else
            {
            break
            }
        }

    if ($resultsSeen -gt 0 -and $onDomainSeen -eq 0)
        {
        Write-Host "[!] DuckDuckGo returned results, but none of them are on $Domain."
        }
    Write-Host "[*] DuckDuckGo: $($links.Count) $FileType files found"

    $links
}

#The part of a link that identifies the file: host (and port, when it is not the usual one), path and query.
#"http://" or "https://" is left out, so the two addresses of one file compare as equal.
function Get-PowerMetaLinkKey {
    param([string]$Link)

    $uri = $null
    if (-not [System.Uri]::TryCreate($Link, [System.UriKind]::Absolute, [ref]$uri)) { return $Link }

    $key = $uri.Host.ToLowerInvariant()
    if (-not $uri.IsDefaultPort) { $key += ':' + $uri.Port }
    return ($key + $uri.PathAndQuery)
}

#Reads the answer of the Internet Archive's index: one "address timestamp" pair per line.
#Returns one object per file on the domain, with Link (the address on the site) and ArchiveCopy (the Archive's copy of it).
function ConvertFrom-PowerMetaArchiveIndex {
    param([string]$Text, [string]$Domain, [string[]]$FileTypes)

    $entries = New-Object System.Collections.Generic.List[object]
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    if (-not $Text) { return }

    foreach ($line in ($Text -split "`n"))
        {
        $parts = @($line.Trim() -split '\s+')
        if ($parts.Count -ne 2) { continue }
        $original = $parts[0]
        $timestamp = $parts[1]
        if ($timestamp -notmatch '^\d{14}$') { continue }

        #Captured addresses often carry the default port (http://example.com:80/a.pdf); AbsoluteUri drops it
        $uri = $null
        if (-not [System.Uri]::TryCreate($original, [System.UriKind]::Absolute, [ref]$uri)) { continue }
        $link = $uri.AbsoluteUri

        $wanted = $false
        foreach ($type in $FileTypes)
            {
            if (Test-PowerMetaFileLink -Link $link -Domain $Domain -FileType $type)
                {
                $wanted = $true
                break
                }
            }
        if (-not $wanted) { continue }

        if ($seen.Add($link))
            {
            $entries.Add((New-Object PSObject -Property @{ Link = $link; ArchiveCopy = ('https://web.archive.org/web/' + $timestamp + 'id_/' + $original) }))
            }
        }
    $entries
}

#Asks the Internet Archive's Wayback Machine index for the addresses it has captured on the domain and its
#sub-domains that name a file of a wanted type (at most -MaxResults of them). The index is a service made to
#be queried by programs, and one request covers all file types. Files it lists may have been removed from
#the site since.
function Search-PowerMetaArchive {
    param(
        [string]$Domain,
        [string[]]$FileTypes,
        [string]$UserAgent = "",
        [int]$MaxResults = 10000
    )

    Write-Host "[*] Asking the Internet Archive which files it has seen on $Domain"

    #The index applies the filter, a Java regular expression, to the whole address. The pattern skips the
    #host name (a host such as www.doc.gov must not count as a file type) and then looks for ".pdf" (or
    #another wanted type) that is not followed by a letter or digit.
    $types = (@($FileTypes | ForEach-Object { [regex]::Escape($_) }) -join '|')
    $filter = 'original:(?i)[a-z]+://[^/?#]+[/?].*\.(' + $types + ')([^a-z0-9].*)?'
    $url = 'https://web.archive.org/cdx/search/cdx?url=' + [System.Uri]::EscapeDataString($Domain) +
        '&matchType=domain&fl=original,timestamp&collapse=urlkey&gzip=false&filter=statuscode:200&filter=' +
        [System.Uri]::EscapeDataString($filter) + '&limit=' + $MaxResults

    $text = Get-PowerMetaWebPage -Url $url -UserAgent $UserAgent -TimeoutSec 180
    if ($null -eq $text)
        {
        Write-Host "[!] The Internet Archive did not answer. Its index is sometimes slow or busy; try again later."
        return
        }

    $entries = @(ConvertFrom-PowerMetaArchiveIndex -Text $text -Domain $Domain -FileTypes $FileTypes)
    Write-Host "[*] Internet Archive: $($entries.Count) files found"

    #The index stops at the limit without saying so
    $lineCount = @($text -split "`n" | Where-Object { $_.Trim() -ne '' }).Count
    if ($lineCount -ge $MaxResults)
        {
        Write-Host "[!] The Internet Archive has more than $MaxResults matching addresses for $Domain. Only the first $MaxResults were read, so this list is incomplete."
        }

    $entries
}

#Turns the last part of a link into a file name that is safe on Windows and in PowerShell.
#A name with an ending always ends in one of the wanted file types. The site must not be able to choose
#another one: a download saved as setup.exe or run.bat could be started by mistake. So
#get.php?file=report.pdf is saved as get.php.pdf, and a link that names no wanted type gets ".download" added.
function Get-PowerMetaFileName {
    param([string]$Link, [string[]]$FileTypes = @())

    if ($null -eq $FileTypes -or $FileTypes.Count -eq 0) { $FileTypes = @('pdf', 'docx', 'xlsx', 'doc', 'xls', 'pptx', 'ppt') }

    $name = ''
    $pathAndQuery = ''
    $uri = $null
    if ([System.Uri]::TryCreate($Link, [System.UriKind]::Absolute, [ref]$uri))
        {
        $path = $uri.AbsolutePath
        $name = $path.Substring($path.LastIndexOf('/') + 1)
        $pathAndQuery = $uri.PathAndQuery
        }

    #Keep letters, digits and a few harmless symbols; everything else becomes "_"
    $name = $name -replace '[^A-Za-z0-9._%+=@,~() \-]', '_'
    $name = $name.Trim().TrimEnd('.', ' ')

    $ending = ''
    $dot = $name.LastIndexOf('.')
    if ($dot -ge 0) { $ending = $name.Substring($dot + 1).ToLowerInvariant() }
    if ($FileTypes -notcontains $ending)
        {
        #The type that made the link a wanted one, if it names one (get.php?file=report.pdf)
        $named = ''
        foreach ($type in $FileTypes)
            {
            if ([regex]::IsMatch($pathAndQuery, ('\.' + [regex]::Escape($type) + '(?![A-Za-z0-9])'), 'IgnoreCase'))
                {
                $named = $type
                break
                }
            }
        if ($named -ne '')
            {
            if ($name -eq '') { $name = 'download' }
            $name = $name + '.' + $named
            }
        elseif ($dot -ge 0)
            {
            $name = $name + '.download'
            }
        }

    if ($name.Length -gt 150)
        {
        $extension = [System.IO.Path]::GetExtension($name)
        if ($extension.Length -gt 20) { $extension = '' }
        $name = $name.Substring(0, 150 - $extension.Length) + $extension
        }

    if ($name -eq '' -or $name -match '^[. ]+$') { $name = 'download' }
    #Windows device names, also when spaces come before the dot ("nul .pdf")
    if ($name -match '^(con|prn|aux|nul|com[0-9]|lpt[0-9]) *(\.|$)') { $name = '_' + $name }

    #A download must never be taken for ExifTool itself, for its configuration file (.ExifTool_config),
    #or for a command-line option (a name that starts with "-")
    if ($name -match '^([.\-]|exiftool)') { $name = '_' + $name }
    $name
}

#Downloads one address to a file. Returns an empty string when it worked, otherwise the reason it failed.
function Save-PowerMetaFile {
    param(
        [string]$Link,
        [string]$Destination,
        [string]$UserAgent = "",
        [bool]$SkipCertificateCheck = $false
    )

    # The progress bar makes Invoke-WebRequest very slow in Windows PowerShell
    $ProgressPreference = 'SilentlyContinue'

    #Windows PowerShell reads -OutFile as a wildcard pattern, so an output folder with [ or ] in its
    #path would make every download fail. Such files are fetched to a temporary name and then moved.
    $target = $Destination
    $viaTemp = [System.Management.Automation.WildcardPattern]::ContainsWildcardCharacters($Destination)
    if ($viaTemp) { $target = Join-Path ([System.IO.Path]::GetTempPath()) ('powermeta-' + [System.Guid]::NewGuid().ToString('N') + '.tmp') }

    $request = @{ Uri = $Link; UseBasicParsing = $true; OutFile = $target; TimeoutSec = 300; ErrorAction = 'Stop' }
    if ($UserAgent -ne "") { $request['UserAgent'] = $UserAgent }
    if ($SkipCertificateCheck) { $request['SkipCertificateCheck'] = $true }

    try
        {
        Invoke-WebRequest @request
        if ($viaTemp) { [System.IO.File]::Move($target, $Destination) }
        return ''
        }
    catch
        {
        $reason = [string]$_.Exception.Message
        if ($viaTemp -and (Test-Path -LiteralPath $target)) { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue }
        if ($reason -eq '') { $reason = 'the download failed' }
        return $reason
        }
}

#Function to download files from a webserver
Function Get-TargetFiles{
        Param(

        [Parameter(Position = 0, Mandatory = $false)]
        [array]
        $targetlinks = @(),

        [Parameter(Position = 1, Mandatory = $false)]
        [string]
        $OutputDir = "",

        [Parameter(Position = 2, Mandatory = $false)]
        [string]
        $UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",

        #Optional: for links found through the Internet Archive, where the Archive keeps its copy.
        #It is used when a file can no longer be downloaded from the site itself.
        [Parameter(Mandatory = $false)]
        [System.Collections.IDictionary]
        $ArchiveCopies = $null,

        #Optional: the wanted file types. Every saved file gets a name that ends in one of them.
        [Parameter(Mandatory = $false)]
        [string[]]
        $FileTypes = @()

        )

        $links = @($targetlinks | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne '' })

        Write-Output ('[*] Downloading ' + $links.Count + ' files now.')
        if ($links.Count -eq 0) { return }

        if ($OutputDir -eq "")
            {
                $OutputDir = Get-Date -format yyyy-MM-dd-HHmmss
            }

        # Testing to see if the output directory exists. If not, we'll create it.
        If(-not (Test-Path -LiteralPath $OutputDir -PathType Container))
            {
                Write-Output "[*] The output directory $OutputDir does not exist. Creating directory $OutputDir."
                New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
            }
        If(-not (Test-Path -LiteralPath $OutputDir -PathType Container))
            {
                Write-Output "[!] The output directory $OutputDir could not be created."
                return
            }

        # Getting the full path of the output dir.
        $OutputPath = Convert-Path -LiteralPath $OutputDir

        # The progress bar makes Invoke-WebRequest very slow in Windows PowerShell
        $ProgressPreference = 'SilentlyContinue'

        # Target servers often have expired or self-signed certificates, so certificate errors are
        # ignored while the files download - and only then. The previous setting is put back afterwards.
        $isCore = ($PSVersionTable.PSVersion.Major -ge 6)
        $restoreCallback = $false
        $previousCallback = $null
        if (-not $isCore)
            {
            try
                {
                if (-not ('PowerMetaTrustAllCertificates' -as [type]))
                    {
                    Add-Type -TypeDefinition @'
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public static class PowerMetaTrustAllCertificates {
    private static bool Accept(object sender, X509Certificate certificate, X509Chain chain, SslPolicyErrors errors) {
        return true;
    }
    public static RemoteCertificateValidationCallback Callback = new RemoteCertificateValidationCallback(Accept);
}
'@
                    }
                $previousCallback = [System.Net.ServicePointManager]::ServerCertificateValidationCallback
                [System.Net.ServicePointManager]::ServerCertificateValidationCallback = [PowerMetaTrustAllCertificates]::Callback
                $restoreCallback = $true
                }
            catch
                {
                Write-Output "[!] Could not relax certificate checking; downloads from servers with invalid certificates will fail."
                }
            }

        $downloaded = 0
        $failed = 0
        try
            {
            foreach ($link in $links)
                {
                $uri = $null
                if (-not [System.Uri]::TryCreate($link, [System.UriKind]::Absolute, [ref]$uri) -or ($uri.Scheme -ne 'http' -and $uri.Scheme -ne 'https'))
                    {
                    Write-Output "[!] Skipping $link (not an http or https link)"
                    $failed++
                    continue
                    }

                #When downloading files if a file has the same name a number is added to it
                $filename = Get-PowerMetaFileName -Link $link -FileTypes $FileTypes
                $candidate = $filename
                $number = 1
                while (Test-Path -LiteralPath (Join-Path $OutputPath $candidate))
                    {
                    $candidate = [System.IO.Path]::GetFileNameWithoutExtension($filename) + '-' + $number + [System.IO.Path]::GetExtension($filename)
                    $number++
                    }
                $destination = Join-Path $OutputPath $candidate

                Write-Output "Now Downloading $link"
                $problem = [string](Save-PowerMetaFile -Link $link -Destination $destination -UserAgent $UserAgent -SkipCertificateCheck $isCore)

                #A file that is gone from the site may still be in the Internet Archive
                if ($problem -ne '' -and $null -ne $ArchiveCopies -and $ArchiveCopies.Contains($link))
                    {
                    Write-Output "[*] Not available from the site ($problem). Trying the copy in the Internet Archive."
                    Start-Sleep -Seconds 1

                    #The Internet Archive has a valid certificate, so for this request it is checked as usual
                    if ($restoreCallback) { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $previousCallback }
                    try
                        {
                        $problem = [string](Save-PowerMetaFile -Link ([string]$ArchiveCopies[$link]) -Destination $destination -UserAgent $UserAgent -SkipCertificateCheck $false)
                        }
                    finally
                        {
                        if ($restoreCallback) { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = [PowerMetaTrustAllCertificates]::Callback }
                        }
                    }

                if ($problem -eq '')
                    {
                    $downloaded++
                    }
                else
                    {
                    Write-Output "[!] Could not download $link : $problem"
                    $failed++
                    }
                }
            }
        finally
            {
            if ($restoreCallback) { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $previousCallback }
            }

        Write-Output "[*] Downloaded $downloaded files to $OutputPath ($failed failed)."
}

#Finds exiftool next to this script or on the PATH. The current directory is deliberately not searched:
#it may be a folder of files that were downloaded from the target.
function Get-PowerMetaExifTool {
    if ($env:OS -eq 'Windows_NT') { $name = 'exiftool.exe' } else { $name = 'exiftool' }

    if ($PSScriptRoot)
        {
        $candidate = Join-Path $PSScriptRoot $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return (Convert-Path -LiteralPath $candidate) }
        }

    $command = Get-Command -Name exiftool -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Path }
    return $null
}

#ExifTool before 12.24 can be made to run code by a crafted file (CVE-2021-22204),
#and PowerMeta runs it on files downloaded from the target. Returns $true if the copy is safe to use.
function Test-PowerMetaExifTool {
    param([string]$ExifToolPath)

    $reported = ''
    try { $reported = ("" + (& $ExifToolPath -ver | Select-Object -First 1)).Trim() } catch { $reported = '' }

    $version = $null
    try { $version = [version]$reported } catch { $version = $null }

    if ($null -eq $version)
        {
        Write-Host "[!] $ExifToolPath did not report a version, so it cannot be used. On Windows the 'exiftool_files' folder must sit next to exiftool.exe. Run Install-ExifTool to get a working copy."
        return $false
        }
    if ($version -lt [version]'12.24')
        {
        Write-Host "[!] $ExifToolPath is ExifTool $reported. Versions before 12.24 can be made to run code by a crafted file (CVE-2021-22204), so it will not be used on downloaded files. Run Install-ExifTool to get the current release."
        return $false
        }
    return $true
}

#Runs exiftool on every file in a folder and returns its output lines.
#ExifTool writes UTF-8, so the console encoding is switched while it runs.
function Invoke-PowerMetaExifTool {
    param([string]$ExifToolPath, [string[]]$Options, [string]$Folder)

    $previousEncoding = $null
    try
        {
        $previousEncoding = [Console]::OutputEncoding
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        }
    catch
        {
        $previousEncoding = $null
        }

    try
        {
        #"-ext *" makes exiftool read every file, also those without a known extension.
        #The '*' and the folder have to be quoted strings right here: PowerShell 7 on Linux and macOS expands
        #wildcards in arguments that come from a variable, but never in a quoted string.
        & $ExifToolPath $Options -ext '*' "$Folder"
        }
    finally
        {
        if ($null -ne $previousEncoding)
            {
            try { [Console]::OutputEncoding = $previousEncoding } catch { }
            }
        }
}

#A spreadsheet program runs a cell that starts with = + - or @ as a formula, and the metadata comes from
#downloaded files. Such a cell gets an apostrophe in front, which makes it plain text. Plain numbers such
#as -5 or +0.3 are left as they are. Takes the whole CSV text (lines joined with a line feed) and returns it.
function ConvertTo-PowerMetaInertCsv {
    param([string]$Text)

    #One cell and what ends it: a quoted cell (quotes inside it are doubled) or a plain one, then a comma,
    #a line feed or the end of the text
    $cellPattern = '\G(?:"(?<q>(?:[^"]|"")*)"|(?<u>[^,\n]*))(?<d>,|\n|\z)'
    $makeInert = [System.Text.RegularExpressions.MatchEvaluator] {
        param($found)
        $quoted = $found.Groups['q'].Success
        if ($quoted) { $cell = $found.Groups['q'].Value } else { $cell = $found.Groups['u'].Value }
        if ($cell -match '^[=+\-@\t\r\n]' -and $cell -notmatch '^[+-]?[0-9]+([.,][0-9]+)?\z') { $cell = "'" + $cell }
        if ($quoted) { $cell = '"' + $cell + '"' }
        $cell + $found.Groups['d'].Value
    }
    [regex]::Replace($Text, $cellPattern, $makeInert)
}

#Use exiftool to extract metadata from each file
Function ExtractMetadata{
        Param(

        [Parameter(Position = 0, Mandatory = $false)]
        [string]
        $OutputDir = "",

        [Parameter(Position = 1, Mandatory = $false)]
        [string]
        $ExtractAllToCsv = ""
        )

        if ($OutputDir -eq "" -or -not (Test-Path -LiteralPath $OutputDir -PathType Container))
            {
            Write-Output "[*] $OutputDir does not exist! Canceling metadata extraction."
            return
            }

        $exiftool = Get-PowerMetaExifTool
        if (-not $exiftool)
            {
            Write-Output "[*] ExifTool was not found next to PowerMeta.ps1 or on the PATH. Run Install-ExifTool to download the current release, then run: ExtractMetadata -OutputDir `"$OutputDir`""
            return
            }
        if (-not (Test-PowerMetaExifTool $exiftool))
            {
            return
            }

        $OutputPath = Convert-Path -LiteralPath $OutputDir
        #A path that ends in a backslash would swallow the closing quote when it is passed to exiftool
        if ($OutputPath.EndsWith('\')) { $OutputPath = $OutputPath + '.' }

        #If "ExtractAllToCsv flag is set extract all metadata from files
        if ($ExtractAllToCsv -ne "")
            {
            Write-Output "[*] Now extracting metadata from $OutputDir"
            $csv = @(Invoke-PowerMetaExifTool -ExifToolPath $exiftool -Options @('-csv', '-q', '-q') -Folder $OutputPath)
            if ($csv.Count -lt 2)
                {
                Write-Output "[*] ExifTool found no metadata in $OutputDir."
                return
                }
            try
                {
                #Cells that a spreadsheet program would run as a formula are made plain text
                $csv = @((ConvertTo-PowerMetaInertCsv -Text ($csv -join "`n")) -split "`n")
                Set-Content -LiteralPath $ExtractAllToCsv -Value $csv -Encoding UTF8 -ErrorAction Stop
                Write-Output ('[*] Metadata for ' + ($csv.Count - 1) + " files was written to $ExtractAllToCsv")
                }
            catch
                {
                Write-Output "[!] Could not write $ExtractAllToCsv : $($_.Exception.Message)"
                }
            return
            }

        #If we are not extracting all Metadata just get the Author and Creator for possible usernames.
        Write-Output "[*] Now extracting metadata from $OutputDir"
        $json = (@(Invoke-PowerMetaExifTool -ExifToolPath $exiftool -Options @('-j', '-q', '-q', '-Author', '-Creator') -Folder $OutputPath) -join "`n").Trim()

        $records = @()
        if ($json -ne '')
            {
            try
                {
                $parsed = ConvertFrom-Json -InputObject $json
                $records = @($parsed | Where-Object { $null -ne $_ })
                }
            catch
                {
                Write-Output "[!] The output of ExifTool could not be read: $($_.Exception.Message)"
                return
                }
            }

        $allmeta = @()
        foreach ($record in $records)
            {
            foreach ($tag in @('Author', 'Creator'))
                {
                $property = $record.PSObject.Properties[$tag]
                if ($null -eq $property -or $null -eq $property.Value) { continue }
                foreach ($value in @($property.Value))
                    {
                    $text = ([string]$value).Trim()
                    if ($text -ne '') { $allmeta += $text }
                    }
                }
            }
        $uniquemeta = @($allmeta | Sort-Object -Unique)

        Write-Output ('[*] ExifTool read ' + $records.Count + ' files.')
        Write-Output "`n[*] Extracted 'Author' and 'Creator' metadata"
        if ($uniquemeta.Count -eq 0)
            {
            Write-Output "[*] No 'Author' or 'Creator' metadata was found."
            }
        else
            {
            $uniquemeta
            }
        Write-Output "`n"
}

#SHA-256 of a file as lower-case hex
function Get-PowerMetaSha256 {
    param([string]$Path)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try
        {
        $bytes = $sha.ComputeHash($stream)
        }
    finally
        {
        $stream.Dispose()
        $sha.Dispose()
        }
    ([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

#Downloads the current official ExifTool for Windows and unpacks it next to this script
Function Install-ExifTool{

    <#
    .SYNOPSIS
    Downloads the current official ExifTool package for Windows, checks it against the SHA-256 checksum published on exiftool.org, and unpacks exiftool.exe and its exiftool_files folder next to PowerMeta.ps1.

    .PARAMETER Destination
    The folder to install into. Defaults to the folder PowerMeta.ps1 is in.

    .Example

    C:\PS> Install-ExifTool
    #>

        Param(

        [Parameter(Position = 0, Mandatory = $false)]
        [string]
        $Destination = ""
        )

        if ($env:OS -ne 'Windows_NT')
            {
            Write-Output "[*] Install-ExifTool is for Windows. On Linux or macOS install ExifTool with your package manager, for example 'apt install libimage-exiftool-perl' or 'brew install exiftool'."
            return
            }

        if ($Destination -eq "")
            {
            if ($PSScriptRoot) { $Destination = $PSScriptRoot } else { $Destination = (Get-Location).Path }
            }
        if (-not (Test-Path -LiteralPath $Destination -PathType Container))
            {
            New-Item -ItemType Directory -Path $Destination -Force | Out-Null
            }
        $Destination = Convert-Path -LiteralPath $Destination

        $ProgressPreference = 'SilentlyContinue'
        #A plain agent name: the download mirror answers browser-like agents with a web page instead of the file
        $agent = 'PowerMeta'
        $work = Join-Path ([System.IO.Path]::GetTempPath()) ('powermeta-exiftool-' + [System.Guid]::NewGuid().ToString('N'))

        try
            {
            $version = ("" + (Get-PowerMetaWebPage -Url 'https://exiftool.org/ver.txt' -UserAgent $agent)).Trim()
            if ($version -notmatch '^\d+\.\d+$')
                {
                Write-Output "[!] Could not read the current ExifTool version from https://exiftool.org/ver.txt. Nothing was installed."
                return
                }

            if ([System.Environment]::Is64BitOperatingSystem) { $bits = '64' } else { $bits = '32' }
            $package = "exiftool-${version}_$bits"
            $zipName = "$package.zip"

            $checksums = "" + (Get-PowerMetaWebPage -Url 'https://exiftool.org/checksums.txt' -UserAgent $agent)
            $match = [regex]::Match($checksums, 'SHA2-256\(' + [regex]::Escape($zipName) + '\)\s*=\s*([0-9a-fA-F]{64})')
            if (-not $match.Success)
                {
                Write-Output "[!] No SHA-256 checksum for $zipName is listed on https://exiftool.org/checksums.txt. Nothing was installed."
                return
                }
            $expected = $match.Groups[1].Value.ToLowerInvariant()

            New-Item -ItemType Directory -Path $work -Force | Out-Null
            $zipPath = Join-Path $work $zipName
            Write-Output "[*] Downloading ExifTool $version ($zipName)"
            Invoke-WebRequest -Uri "https://sourceforge.net/projects/exiftool/files/$zipName/download" -UserAgent $agent -UseBasicParsing -OutFile $zipPath -TimeoutSec 600 -ErrorAction Stop

            $actual = Get-PowerMetaSha256 $zipPath
            if ($actual -ne $expected)
                {
                Write-Output "[!] The download does not match the checksum published on exiftool.org (expected $expected, got $actual). Nothing was installed."
                return
                }
            Write-Output "[*] Checksum verified."

            $unpacked = Join-Path $work 'unpacked'
            try { Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop } catch { }
            [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $unpacked)

            #The package holds "exiftool(-k).exe", which must be renamed to exiftool.exe for command-line use, and "exiftool_files"
            $launcher = Join-Path (Join-Path $unpacked $package) 'exiftool(-k).exe'
            $files = Join-Path (Join-Path $unpacked $package) 'exiftool_files'
            if (-not (Test-Path -LiteralPath $launcher -PathType Leaf) -or -not (Test-Path -LiteralPath $files -PathType Container))
                {
                Write-Output "[!] The ExifTool package does not have the expected layout. Nothing was installed."
                return
                }

            $targetFiles = Join-Path $Destination 'exiftool_files'
            if (Test-Path -LiteralPath $targetFiles)
                {
                Write-Output "[*] Replacing the existing $targetFiles"
                Remove-Item -LiteralPath $targetFiles -Recurse -Force -ErrorAction Stop
                }
            Copy-Item -LiteralPath $files -Destination $targetFiles -Recurse -Force -ErrorAction Stop
            Copy-Item -LiteralPath $launcher -Destination (Join-Path $Destination 'exiftool.exe') -Force -ErrorAction Stop

            Write-Output "[*] ExifTool $version was installed to $Destination"
            }
        catch
            {
            Write-Output "[!] ExifTool could not be installed: $($_.Exception.Message)"
            }
        finally
            {
            if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
            }
}
