$ErrorActionPreference = 'Stop'
$repo = 'https://github.com/mikhail-avis/Vibe-Finder.git'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$paths = @('.gitignore','README.md','GITHUB_SETUP.md','app.py','engine.py','discovery.py','updater.py','version.py','requirements.txt','run-windows.bat','update-source.json','scripts','tests','library')
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is required: https://git-scm.com/download/win' }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI is required: https://cli.github.com/' }
gh auth status
if ($LASTEXITCODE -ne 0) { gh auth login --hostname github.com --git-protocol https --web; if($LASTEXITCODE -ne 0) { throw 'GitHub login unsuccessful.' } }
gh auth setup-git
$work = Join-Path $env:TEMP ('vibe-finder-publish-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
  git clone $repo $work
  if ($LASTEXITCODE -ne 0) { throw 'Could not clone repository.' }
  foreach($p in $paths) {
    $src = Join-Path $root $p
    if(-not (Test-Path $src)) { throw "Missing package file: $p" }
    Copy-Item -Path $src -Destination (Join-Path $work $p) -Recurse -Force
  }
  New-Item -ItemType Directory -Path (Join-Path $work 'releases') -Force | Out-Null
  Copy-Item (Join-Path $root 'releases\v0.3.0.zip') (Join-Path $work 'releases\v0.3.0.zip') -Force
  Push-Location $work
  try {
    git add -- .gitignore README.md GITHUB_SETUP.md app.py engine.py discovery.py updater.py version.py requirements.txt run-windows.bat update-source.json scripts tests library/PUT_YOUR_AUDIO_HERE.txt releases/v0.3.0.zip
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
      git commit -m 'Publish Vibe Finder v0.3 application and release archive'
      if($LASTEXITCODE -ne 0) { throw 'Could not commit files. Configure git user.name and user.email.' }
      git push origin HEAD:main
      if($LASTEXITCODE -ne 0) { throw 'Cannot push. Check repo permissions.' }
    }
    Copy-Item (Join-Path $root 'releases\latest.json') (Join-Path $work 'releases\latest.json') -Force
    git add -- releases/latest.json
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
      git commit -m 'Publish Vibe Finder v0.3 online update manifest'
      if($LASTEXITCODE -ne 0) { throw 'Could not commit release manifest.' }
      git push origin HEAD:main
      if($LASTEXITCODE -ne 0) { throw 'Could not push manifest.' }
    }
    Write-Host 'Published Vibe Finder source, release ZIP, and manifest.' -ForegroundColor Green
  } finally { Pop-Location }
} finally {
  if(Test-Path $work) { Remove-Item $work -Recurse -Force }
}
