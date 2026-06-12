param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "h5", "results", "status", "watchlist", "brief", "h5-report")]
    [string]$Phase,

    [string]$Url = "https://gpi-lite-watchlist-h3tdqmb7jq-ew.a.run.app",

    [string]$KeyFile = ".\.secrets\gpi_api_key.txt"
)

$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

function Read-GpiKey {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "Clé API absente : $Path"
    }

    return (Get-Content $Path -Raw).Trim()
}

function Invoke-GpiGet {
    param([string]$Endpoint)

    $FullUrl = "$Url$Endpoint"
    Write-Host "GET $Endpoint" -ForegroundColor Cyan

    curl.exe -sS $FullUrl
}

function Invoke-GpiPostProtected {
    param([string]$Endpoint)

    $Key = Read-GpiKey -Path $KeyFile
    $FullUrl = "$Url$Endpoint"

    Write-Host "POST $Endpoint" -ForegroundColor Cyan

    curl.exe -sS -X POST $FullUrl `
        -H "Content-Type: application/json" `
        -H "X-API-Key: $Key" `
        --data "{}"
}

switch ($Phase) {
    "status" {
        Invoke-GpiGet "/action/status"
        break
    }

    "watchlist" {
        Invoke-GpiGet "/action/watchlist"
        break
    }

    "brief" {
        Invoke-GpiGet "/action/brief"
        break
    }

    "h5-report" {
        Invoke-GpiGet "/action/h5-report?limit=12"
        break
    }

    "morning" {
        $run = Invoke-GpiPostProtected "/drive/run/morning"
        Write-Host $run

        Write-Host "`n=== Watchlist après morning ===" -ForegroundColor Yellow
        Invoke-GpiGet "/action/watchlist"
        break
    }

    "h5" {
        $run = Invoke-GpiPostProtected "/drive/run/h5"
        Write-Host $run

        Write-Host "`n=== Brief après H-5 ===" -ForegroundColor Yellow
        Invoke-GpiGet "/action/brief"

        Write-Host "`n=== Rapport H-5 lisible ===" -ForegroundColor Yellow
        Invoke-GpiGet "/action/h5-report?limit=12"

        Write-Host "`n=== Vérification KPI paper ===" -ForegroundColor Yellow
        $statusRaw = Invoke-GpiGet "/action/status"
        Write-Host $statusRaw

        try {
            $status = $statusRaw | ConvertFrom-Json

            if ($status.paper_trading_count -ne $status.paper_kpi_global_decisions) {
                Write-Host "`nKPI désynchronisé : lancement results requis." -ForegroundColor Yellow
                Write-Host "paper_trading_count =" $status.paper_trading_count
                Write-Host "paper_kpi_global_decisions =" $status.paper_kpi_global_decisions

                Invoke-GpiPostProtected "/drive/run/results-compact"

                Write-Host "`n=== Statut après results ===" -ForegroundColor Yellow
                Invoke-GpiGet "/action/status"
            } else {
                Write-Host "`nKPI paper aligné : OK" -ForegroundColor Green
            }
        } catch {
            Write-Host "Impossible de parser le status JSON : $($_.Exception.Message)" -ForegroundColor Red
        }

        break
    }

    "results" {
        $run = Invoke-GpiPostProtected "/drive/run/results-compact"
        Write-Host $run

        Write-Host "`n=== Statut après results ===" -ForegroundColor Yellow
        Invoke-GpiGet "/action/status"
        break
    }
}

