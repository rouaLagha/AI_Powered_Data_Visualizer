<#
Helper PowerShell to get a Power BI token and call the Python publisher.
#>

param(
    [Parameter(Mandatory = $true)][string]$RdlPath,
    [ValidateSet('Abort', 'Overwrite', 'CreateOrOverwrite')][string]$NameConflict = 'Abort',
    [int]$TimeoutSeconds = 300,
    [string]$ConfigPath = (Join-Path -Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)) -ChildPath 'config\llm_config.json')
)

$ErrorActionPreference = 'Stop'

try {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $pythonScript = Join-Path -Path $scriptDir -ChildPath 'publish_powerbi_rdl.py'

    $token = $env:POWERBI_ACCESS_TOKEN
    if ($token) {
        Write-Host 'Using POWERBI_ACCESS_TOKEN from the current session.'
    }

    if (-not $token -and (Test-Path -LiteralPath $ConfigPath)) {
        try {
            $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
            $powerbiConfig = $config.powerbi_service
            if ($powerbiConfig -and $powerbiConfig.access_token) {
                $configToken = [string]$powerbiConfig.access_token
                if ($configToken.StartsWith('env:')) {
                    $envName = $configToken.Substring(4).Trim()
                    if ($envName) {
                        $token = [string](Get-Item -Path "Env:$envName" -ErrorAction SilentlyContinue).Value
                        if ($token) {
                            Write-Host "Using Power BI token from environment variable $envName referenced in llm_config.json."
                        }
                    }
                } else {
                    $token = $configToken
                    if ($token) {
                        Write-Host 'Using Power BI token from llm_config.json.'
                    }
                }
            }
        } catch {
            Write-Warning ("Could not read Power BI token from config file {0}: {1}" -f $ConfigPath, $_)
        }
    }

    $tenantId = $env:POWERBI_TENANT_ID
    $clientId = $env:POWERBI_CLIENT_ID
    $clientSecret = $env:POWERBI_CLIENT_SECRET

    if (-not $token -and $tenantId -and $clientId -and $clientSecret) {
        Write-Host 'Service principal credentials detected, requesting token...'
        $tokenEndpoint = "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token"
        $body = @{
            grant_type    = 'client_credentials'
            client_id     = $clientId
            client_secret = $clientSecret
            scope         = 'https://analysis.windows.net/powerbi/api/.default'
        }
        $resp = Invoke-RestMethod -Method Post -Uri $tokenEndpoint -Body $body -ContentType 'application/x-www-form-urlencoded'
        $token = $resp.access_token
    }

    if (-not $token) {
        throw 'No Power BI token available. Set POWERBI_ACCESS_TOKEN or POWERBI_TENANT_ID/POWERBI_CLIENT_ID/POWERBI_CLIENT_SECRET.'
    }

    $env:POWERBI_ACCESS_TOKEN = $token
    $env:RDL_PATH = $RdlPath

    & python $pythonScript --rdl $RdlPath --name-conflict $NameConflict --timeout $TimeoutSeconds
    exit $LASTEXITCODE
}
catch {
    Write-Error ('Publishing failed: {0}' -f $_)
    exit 3
}
