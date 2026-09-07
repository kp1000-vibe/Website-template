# One time setup for Windows. Safe to re-run: it never overwrites a config you
# have edited. Run it from PowerShell inside the project folder:
#
#     .\setup.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($msg) { Write-Host "`n$msg" -ForegroundColor White -BackgroundColor DarkBlue }

Say "1/5  checking python"
$py = $null
foreach ($c in @("python", "python3", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) {
        $ok = & $c -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $v = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])"
            $py = $c
            Write-Host "  using $c (python $v)"
            break
        }
    }
}
if (-not $py) {
    Write-Host "  need python 3.10 or newer." -ForegroundColor Red
    Write-Host "  install it from https://www.python.org/downloads/ and tick"
    Write-Host "  'Add python.exe to PATH' on the first screen of the installer."
    exit 1
}

Say "2/5  creating the virtual environment"
if (-not (Test-Path ".venv")) { & $py -m venv .venv }
$vpy = ".\.venv\Scripts\python.exe"
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r requirements.txt
Write-Host "  dependencies installed"

Say "3/5  config files"
foreach ($f in @("config", "profile")) {
    if (Test-Path "config\$f.yaml") {
        Write-Host "  config\$f.yaml already exists, left alone"
    } else {
        Copy-Item "config\$f.example.yaml" "config\$f.yaml"
        Write-Host "  created config\$f.yaml from the example"
    }
}
if (-not (Test-Path "config\manual_urls.txt")) {
    Copy-Item "config\manual_urls.txt.example" "config\manual_urls.txt"
}

Say "4/5  browser"
& $vpy -c "from jobagent.fill.browser import find_chrome; import sys; sys.exit(0 if find_chrome(None) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Chrome not found. Install Google Chrome, or set browser.chrome_binary in config\config.yaml"
} else {
    Write-Host "  Chrome found"
}

Say "5/5  checking everything"
& $vpy -m jobagent doctor

Write-Host @"

Next, in order:

  1. notepad config\profile.yaml            your details, and the resume path
  2. `$env:ANTHROPIC_API_KEY = "sk-ant-..."  set your key for this window
  3. .\.venv\Scripts\python -m jobagent verify-boards
  4. .\.venv\Scripts\python -m jobagent chrome      log in once, then leave it open
  5. .\.venv\Scripts\python -m jobagent run
  6. .\.venv\Scripts\python -m jobagent review      then open http://127.0.0.1:8765

"@
