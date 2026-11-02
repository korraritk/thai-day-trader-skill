<#
.SYNOPSIS
Installs the th-trader-* skills from this repo into Claude Code's skills folder.

.DESCRIPTION
Copies each skills\<name>\ folder to <Target>\<name>\ and adds shared\th_market.py and
shared\data\ to it, so every installed skill stands alone. Writes a marker file into each
folder it installs and replaces only folders that carry that marker. It never touches a
folder whose name does not start with th-trader-.

.PARAMETER Target
The skills folder. Default: $HOME\.claude\skills
#>
param(
    [string]$Target = (Join-Path $HOME ".claude\skills")
)

$ErrorActionPreference = "Stop"
$Marker = ".installed-from-thaitraderskills"
$Repo = Split-Path -Parent $PSScriptRoot
$Shared = Join-Path $Repo "shared"

$commit = "unknown"
try {
    $out = & git -C $Repo rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { $commit = "$out".Trim() }
} catch { }

$skills = Get-ChildItem -Path (Join-Path $Repo "skills") -Directory | Where-Object { $_.Name -like "th-trader-*" }
if (-not $skills) {
    Write-Error "No th-trader-* skills found under $Repo\skills."
    exit 1
}

# Check every target before changing anything.
foreach ($skill in $skills) {
    $dest = Join-Path $Target $skill.Name
    if ((Test-Path $dest) -and -not (Test-Path (Join-Path $dest $Marker))) {
        Write-Error "$dest exists but was not installed from ThaiTraderSkills (no $Marker). Move it away first; nothing was changed."
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
foreach ($skill in $skills) {
    $dest = Join-Path $Target $skill.Name
    if (Test-Path $dest) {
        Remove-Item -Recurse -Force -Path $dest
    }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Get-ChildItem -Path $skill.FullName -Force | Where-Object { $_.Name -ne "__pycache__" } |
        Copy-Item -Destination $dest -Recurse -Force
    Copy-Item -Path (Join-Path $Shared "th_market.py") -Destination $dest -Force
    Copy-Item -Path (Join-Path $Shared "data") -Destination $dest -Recurse -Force
    Set-Content -Path (Join-Path $dest $Marker) -Value "ThaiTraderSkills $commit" -Encoding ASCII
    Write-Output "Installed $($skill.Name) to $dest ($commit)"
}
