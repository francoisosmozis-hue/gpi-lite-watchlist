param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "h5", "results", "status", "watchlist", "brief", "h5-report")]
    [string]$Phase
)

$ErrorActionPreference = "Continue"

$Root = "C:\Users\uosmozis\Desktop\Course\gpi-lite-watchlist"
$LogDir = Join-Path $Root "logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
}

$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "gpi_${Phase}_${TS}.log"

Set-Location $Root

"=== GPI TASK START ===" | Tee-Object -FilePath $LogFile
"Phase: $Phase" | Tee-Object -FilePath $LogFile -Append
"Date: $(Get-Date -Format o)" | Tee-Object -FilePath $LogFile -Append
"" | Tee-Object -FilePath $LogFile -Append

try {
    .\scripts\Invoke-GpiOrchestrator.ps1 -Phase $Phase 2>&1 | Tee-Object -FilePath $LogFile -Append
}
catch {
    "ERROR: $($_.Exception.Message)" | Tee-Object -FilePath $LogFile -Append
}

"" | Tee-Object -FilePath $LogFile -Append
"=== GPI TASK END ===" | Tee-Object -FilePath $LogFile -Append
