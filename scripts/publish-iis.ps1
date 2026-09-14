[CmdletBinding()]
param(
    [string]$OutputPath = ".\publish\iis-site",
    [string]$PublishRoot = "C:\inetpub\SeaShellSite",
    [string]$SecretKey = "replace-with-a-real-secret"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedOutputPath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))

$itemsToCopy = @(
    "app.py",
    "install_host.py",
    "wsgi.py",
    "requirements.txt",
    "static",
    "templates"
)

foreach ($item in $itemsToCopy) {
    $sourcePath = Join-Path $projectRoot $item
    if (-not (Test-Path $sourcePath)) {
        throw "Required publish item not found: $sourcePath"
    }
}

if (Test-Path $resolvedOutputPath) {
    Remove-Item -Path $resolvedOutputPath -Recurse -Force
}

New-Item -ItemType Directory -Path $resolvedOutputPath -Force | Out-Null

foreach ($item in $itemsToCopy) {
    Copy-Item -Path (Join-Path $projectRoot $item) -Destination $resolvedOutputPath -Recurse -Force
}

$publishPython = Join-Path $PublishRoot ".venv\Scripts\python.exe"
$publishFastCgi = Join-Path $PublishRoot ".venv\Lib\site-packages\wfastcgi.py"

$webConfig = @"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <appSettings>
    <add key="PYTHONPATH" value="$PublishRoot" />
    <add key="WSGI_HANDLER" value="wsgi.application" />
    <add key="FLASK_ENV" value="production" />
    <add key="FLASK_DEBUG" value="0" />
    <add key="FLASK_SECRET_KEY" value="$SecretKey" />
  </appSettings>
  <system.webServer>
    <handlers>
      <add
        name="Python FastCGI"
        path="*"
        verb="*"
        modules="FastCgiModule"
        scriptProcessor="$publishPython|$publishFastCgi"
        resourceType="Unspecified"
        requireAccess="Script"
      />
    </handlers>
    <staticContent>
      <remove fileExtension=".webp" />
      <mimeMap fileExtension=".webp" mimeType="image/webp" />
    </staticContent>
  </system.webServer>
</configuration>
"@

Set-Content -Path (Join-Path $resolvedOutputPath "web.config") -Value $webConfig -Encoding UTF8

Write-Host "IIS publish layout created at: $resolvedOutputPath"
Write-Host "Configured deploy root: $PublishRoot"