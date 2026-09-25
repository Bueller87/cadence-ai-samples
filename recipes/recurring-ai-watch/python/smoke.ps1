# Run this file as a script; do not paste its statements individually.
# Start the matching Worker in a separate terminal first.
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [ValidateSet('mock', 'live')][string]$Mode = 'mock',
    [string]$TaskList = 'recurring-ai-watch-smoke',
    [string]$Domain = 'cadence-ai-samples'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$WorkflowId = 'watch-smoke-' + [guid]::NewGuid().ToString('N')
$Cli = Join-Path $PSScriptRoot 'main.py'
$Common = @('--domain', $Domain, '--task-list', $TaskList, '--workflow-id', $WorkflowId)

function Invoke-Watch([string[]]$Command) {
    $output = & $Python $Cli @Common @Command
    if ($LASTEXITCODE -ne 0) { throw "Watch command failed: $($Command -join ' ')" }
    return ($output -join "`n").Trim()
}

function Read-Status {
    $text = Invoke-Watch @('status')
    Write-Host $text
    $state = [regex]::Match($text, '(?m)^state: (\w+)\r?$')
    $count = [regex]::Match($text, '(?m)^check count: (\d+)\r?$')
    $report = [regex]::Match($text, '(?m)^latest report: (.+)')
    if (-not $state.Success -or -not $count.Success -or -not $report.Success) {
        throw 'Unrecognized status output'
    }
    return [pscustomobject]@{
        State = $state.Groups[1].Value
        Count = [int]$count.Groups[1].Value
        HasReport = $report.Groups[1].Value.Trim() -ne 'none'
    }
}

try {
    Write-Host (Invoke-Watch @('start', '--mode', $Mode, '--interval', '15'))
    $deadline = (Get-Date).AddMinutes(2)
    do {
        if ((Get-Date) -gt $deadline) { throw 'Timed out waiting for a report' }
        Start-Sleep -Seconds 1
        $status = Read-Status
    } until ($status.State -eq 'WAITING' -and $status.HasReport)

    $before = $status.Count
    Write-Host (Invoke-Watch @('check-now'))
    $deadline = (Get-Date).AddMinutes(1)
    do {
        if ((Get-Date) -gt $deadline) { throw 'Timed out waiting for check-now' }
        Start-Sleep -Seconds 1
        $status = Read-Status
    } until ($status.State -eq 'WAITING' -and $status.Count -gt $before)
    if ($status.Count -ne ($before + 1)) { throw 'Expected exactly one additional check' }

    $beforeStop = $status.Count
    Write-Host (Invoke-Watch @('stop'))
    $deadline = (Get-Date).AddSeconds(30)
    do {
        if ((Get-Date) -gt $deadline) { throw 'Workflow did not reach STOPPED' }
        Start-Sleep -Seconds 1
        $status = Read-Status
        if ($status.Count -ne $beforeStop) { throw 'Another check began during stop' }
    } until ($status.State -eq 'STOPPED')

    Start-Sleep -Seconds 16
    $status = Read-Status
    if ($status.State -ne 'STOPPED' -or $status.Count -ne $beforeStop) {
        throw 'Stopped state or check count changed'
    }
    Write-Host "PASS: CLI smoke for $WorkflowId. Inspect Cadence history for Activity ordering and terminal outcome."
} catch {
    Write-Error "FAIL: $WorkflowId. $($_.Exception.Message) Inspect its Cadence history; it may still be running." -ErrorAction Continue
    exit 1
}
