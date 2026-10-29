<#
.SYNOPSIS
    Downloads the NASA DASHlink FDR archive to the research drive.

.DESCRIPTION
    The archive is ~310 files of roughly 800 MB each (~250 GB). The NASA host
    drops long-running connections, so transfers run a few at a time with
    resume enabled, and anything that stalls is retried on the next pass.

    Safe to re-run and safe to interrupt. Each file is compared against the
    server's reported size, so only what is missing or incomplete gets fetched.

.PARAMETER Destination
    Where the archive lives. Must be on the research drive.

.PARAMETER Concurrency
    Parallel transfers. Above 4 the host starts resetting connections.

.PARAMETER MaxPasses
    How many times to retry stragglers before giving up.

.PARAMETER VerifyOnly
    Report what is complete, partial or missing, then exit.

.EXAMPLE
    .\Download-Dashlink.ps1
    .\Download-Dashlink.ps1 -VerifyOnly
    .\Download-Dashlink.ps1 -Destination "E:\Research\dashlink" -Concurrency 2
#>

[CmdletBinding()]
param(
    [string]$Destination = "D:\Sanju\ResearchData\dashlink",
    [int]$Concurrency = 4,
    [int]$MaxPasses = 20,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"

$ScriptListUrl = "https://c3.ndc.nasa.gov/dashlink/static/media/project/download_flight_data_2.sh"


function Assert-Drive {
    param([string]$Path)

    $letter = (Split-Path -Qualifier $Path).TrimEnd(':')
    $drive = Get-PSDrive -Name $letter -ErrorAction SilentlyContinue

    if (-not $drive) {
        throw @"
Research drive $($letter): is not available.

The DASHlink archive lives on an external drive. Plug it in and re-run, or
point somewhere else:

    .\Download-Dashlink.ps1 -Destination "E:\path\to\dashlink"
"@
    }

    if (-not (Test-Path $Path)) {
        Write-Host "Creating $Path" -ForegroundColor Yellow
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }

    # A drive letter can linger after the volume behind it has gone away.
    try {
        $probe = Join-Path $Path ".write-test"
        Set-Content -Path $probe -Value "ok" -ErrorAction Stop
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
    } catch {
        throw "Drive $($letter): is present but not writable at $Path - check the connection."
    }

    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    Write-Host "Drive $($letter): OK - $freeGB GB free" -ForegroundColor Green
    if ($freeGB -lt 260) {
        Write-Warning "The full archive needs ~250 GB; only $freeGB GB free."
    }
}


function Get-CurlPath {
    foreach ($c in @("$env:SystemRoot\System32\curl.exe",
                     "$env:ProgramFiles\Git\mingw64\bin\curl.exe")) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "curl.exe not found - it ships with Windows 10+ and with Git for Windows."
}


function Get-FileList {
    Write-Host "Fetching file list..." -NoNewline
    $text = (Invoke-WebRequest -Uri $ScriptListUrl -UseBasicParsing).Content
    $files = foreach ($line in $text -split "`n") {
        if ($line -match '^\s*curl\s+(\S+)\s+--output\s+(\S+)') {
            [pscustomobject]@{ Url = $Matches[1]; Name = $Matches[2].Trim() }
        }
    }
    Write-Host " $($files.Count) files"
    if (-not $files) { throw "Could not parse any download URLs from $ScriptListUrl" }
    return $files
}


$script:SizeCache = @{}

function Get-RemoteSize {
    param([string]$Url)

    if ($script:SizeCache.ContainsKey($Url)) { return $script:SizeCache[$Url] }

    try {
        $resp = Invoke-WebRequest -Uri $Url -Method Head -UseBasicParsing -TimeoutSec 30
        $raw = $resp.Headers['Content-Length']
        # Windows PowerShell returns a bare string here, PowerShell 7 a string[].
        # Indexing [0] on the string form gives its first *character*, so
        # "838369594" became 56 and every finished file looked oversized.
        if ($raw -is [array]) { $raw = $raw[0] }
        $size = [int64]"$raw"
    } catch {
        $size = -1
    }

    $script:SizeCache[$Url] = $size
    return $size
}


function Get-Pending {
    param($Files, [string]$Dest, [switch]$Report)

    $pending = @()
    $complete = 0
    $bytesHave = [int64]0

    foreach ($f in $Files) {
        $local = Join-Path $Dest $f.Name
        $localSize = if (Test-Path $local) { (Get-Item $local).Length } else { 0 }

        # Only ask the server about files already on disk - anything missing is
        # pending regardless of size, and skipping those turns a 310-request
        # verification into a handful.
        $remoteSize = if ($localSize -gt 0) { Get-RemoteSize -Url $f.Url } else { 0 }
        $bytesHave += $localSize

        if ($remoteSize -gt 0 -and $localSize -eq $remoteSize) {
            $complete++
            if ($Report) { Write-Host ("  OK        {0}" -f $f.Name) -ForegroundColor DarkGray }
            continue
        }

        # Oversized means a resume appended to a full response. Corrupt, refetch.
        if ($remoteSize -gt 0 -and $localSize -gt $remoteSize) {
            Write-Warning ("{0} is larger than the server copy ({1} vs {2}) - refetching." -f `
                $f.Name, $localSize, $remoteSize)
            if (-not $Report) { Remove-Item $local -Force -ErrorAction SilentlyContinue }
        } elseif ($Report) {
            $state = if ($localSize -gt 0) { "PARTIAL" } else { "MISSING" }
            Write-Host ("  {0,-9} {1}" -f $state, $f.Name) -ForegroundColor Yellow
        }
        $pending += $f
    }

    [pscustomobject]@{
        Pending   = $pending
        Complete  = $complete
        Total     = $Files.Count
        BytesHave = $bytesHave
    }
}


function Invoke-Download {
    param($Files, [string]$Dest, [string]$Curl, [int]$Parallel)

    $queue = [System.Collections.Queue]::Synchronized(
        (New-Object System.Collections.Queue))
    foreach ($f in $Files) { $queue.Enqueue($f) | Out-Null }

    # One process per file, never two on the same file - a second writer
    # appending while the first resumes is what corrupted a file previously.
    $running = @{}
    $done = 0
    $total = $Files.Count

    while ($queue.Count -gt 0 -or $running.Count -gt 0) {
        while ($running.Count -lt $Parallel -and $queue.Count -gt 0) {
            $f = $queue.Dequeue()
            $out = Join-Path $Dest $f.Name
            $args = @("-fL", "--http1.1", "-C", "-", "--retry", "5",
                      "--retry-delay", "10", "--retry-all-errors",
                      "--connect-timeout", "20", "-s", "-S",
                      $f.Url, "-o", $out)
            $proc = Start-Process -FilePath $Curl -ArgumentList $args `
                                  -NoNewWindow -PassThru
            $running[$proc.Id] = @{ Proc = $proc; File = $f }
        }

        Start-Sleep -Seconds 5

        foreach ($id in @($running.Keys)) {
            $entry = $running[$id]
            if ($entry.Proc.HasExited) {
                $running.Remove($id)
                $done++
                $name = $entry.File.Name
                $local = Join-Path $Dest $name
                $size = if (Test-Path $local) { (Get-Item $local).Length } else { 0 }
                $ok = ($size -gt 0 -and $size -eq (Get-RemoteSize -Url $entry.File.Url))
                $tag = if ($ok) { "done" } else { "incomplete" }
                Write-Host ("  [{0}/{1}] {2,-20} {3} ({4:N0} MB)" -f `
                    $done, $total, $name, $tag, ($size / 1MB))
            }
        }
    }
}


# -- Main ---------------------------------------------------------------------
Assert-Drive -Path $Destination

$files = Get-FileList

if ($VerifyOnly) {
    Write-Host "`nVerifying against server sizes...`n"
    $status = Get-Pending -Files $files -Dest $Destination -Report
    Write-Host ("`n{0} of {1} files complete, {2} outstanding - {3:N1} GB on disk" -f `
        $status.Complete, $status.Total, $status.Pending.Count, ($status.BytesHave / 1GB))
    exit 0
}

$curl = Get-CurlPath
Write-Host "curl: $curl"
Write-Host "Destination: $Destination"
Write-Host "Parallel transfers: $Concurrency`n"

for ($pass = 1; $pass -le $MaxPasses; $pass++) {
    $status = Get-Pending -Files $files -Dest $Destination

    Write-Host ("Pass {0}: {1}/{2} complete, {3} outstanding - {4:N1} GB on disk" -f `
        $pass, $status.Complete, $status.Total, $status.Pending.Count,
        ($status.BytesHave / 1GB)) -ForegroundColor Cyan

    if ($status.Pending.Count -eq 0) {
        Write-Host "`nAll files present and size-verified." -ForegroundColor Green
        exit 0
    }

    # Sizes change as files land, so don't trust the cache across passes.
    $script:SizeCache = @{}
    Invoke-Download -Files $status.Pending -Dest $Destination `
                    -Curl $curl -Parallel $Concurrency
}

Write-Warning "Reached the $MaxPasses-pass limit with files still outstanding. Re-run to continue."
exit 1
