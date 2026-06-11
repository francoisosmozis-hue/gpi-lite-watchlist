$API = "https://gpi-lite-watchlist-1084663881709.europe-west1.run.app"
$KEY = $env:GPI_API_KEY
$ROOT = "C:\Users\uosmozis\Desktop\Course\gpi-lite-watchlist"
$FOLDER = Join-Path $ROOT "data\results"

function Post-NoBody($Url) {
    curl.exe -s -X POST $Url -H "X-API-Key: $KEY" -H "Content-Type: application/json" --data-raw "{}"
}

if (-not $KEY) {
    Write-Host "ERREUR : GPI_API_KEY absent."
    Write-Host 'Lance : $env:GPI_API_KEY="TA_CLE_PRIVEE"'
    exit 1
}

$pdfs = Get-ChildItem $FOLDER -Filter *.pdf

if ($pdfs.Count -eq 0) {
    Write-Host "ERREUR : aucun PDF resultats trouve dans $FOLDER"
    exit 1
}

Write-Host "Upload resultats..."
foreach ($pdf in $pdfs) {
    Write-Host "Upload : $($pdf.Name)"
    $form = "file=@$($pdf.FullName);type=application/pdf"
    curl.exe -s -X POST "$API/upload/results" -H "X-API-Key: $KEY" -F $form
    Write-Host ""
}

Write-Host "Fichiers resultats sur le serveur..."
curl.exe -s "$API/files/results" -H "X-API-Key: $KEY"
Write-Host ""

Write-Host "Run results..."
Post-NoBody "$API/run/results"
Write-Host ""

Write-Host "Run audit..."
Post-NoBody "$API/run/audit"
Write-Host ""

Write-Host "Quality..."
Post-NoBody "$API/run/quality"
Write-Host ""

Write-Host "Cloud sync..."
Post-NoBody "$API/gpi/cloud-sync"
Write-Host ""

Write-Host "Summary post-course..."
curl.exe -s "$API/gpi/summary" -H "X-API-Key: $KEY"
Write-Host ""