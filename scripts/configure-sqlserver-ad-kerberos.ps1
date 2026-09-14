<#
.SYNOPSIS
Configures a SQL Server Linux container for Active Directory Kerberos logins.

.DESCRIPTION
Use -Stage ActiveDirectory on the domain controller to register MSSQLSvc SPNs
and create a keytab. Transfer that keytab securely to the Docker host, then
use -Stage Container there to configure the SQL Server container. The
container stage requires the domain controller's FQDN and IPv4 address.

.NOTES
For -Stage ActiveDirectory, run from a domain-connected Windows computer as a
user authorized to update the service account's SPNs. RSAT Active Directory
tools and ktpass.exe are required. For -Stage Container, Docker Desktop is
required. The container hostname must resolve to -SqlHostName from all domain
clients.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$')]
    [string]$DomainFqdn,

    [Parameter()]
    [ValidatePattern('^[a-zA-Z0-9_-]+$')]
    [string]$DomainNetbios = 'DOZER',

    [Parameter(Mandatory)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9-]*$')]
    [string]$SqlHostName,

    [Parameter(Mandatory)]
    [ValidatePattern('^[a-zA-Z0-9._-]+$')]
    [string]$ServiceAccountSamAccountName,

    [Parameter()]
    [ValidateRange(1, 65535)]
    [int]$SqlPort = 1433,

    [Parameter()]
    [string]$ContainerName = 'ISAMU_SQL',

    [Parameter()]
    [string]$KeytabPath = (Join-Path $PSScriptRoot '..\secrets\mssql.keytab'),

    [Parameter()]
    [string]$DomainUser = 'DOZER\mcazabon',

    [Parameter()]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$')]
    [string]$DomainControllerFqdn,

    [Parameter()]
    [ValidatePattern('^(?:\d{1,3}\.){3}\d{1,3}$')]
    [string]$DomainControllerIp,

    [Parameter()]
    [ValidateSet('All', 'ActiveDirectory', 'Container')]
    [string]$Stage = 'All'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Require-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found."
    }
}

if ($Stage -in 'All', 'ActiveDirectory') {
    Require-Command setspn.exe
    Require-Command ktpass.exe

    if (-not (Get-Module -ListAvailable -Name ActiveDirectory)) {
        throw 'The ActiveDirectory PowerShell module is required. Install RSAT: Active Directory Domain Services and Lightweight Directory Tools.'
    }

    Import-Module ActiveDirectory

    $domain = Get-ADDomain -Identity $DomainFqdn
    if ($domain.NetBIOSName -ne $DomainNetbios) {
        throw "The domain '$DomainFqdn' has NetBIOS name '$($domain.NetBIOSName)', not '$DomainNetbios'."
    }

    $serviceAccount = Get-ADUser -Identity $ServiceAccountSamAccountName -Properties ServicePrincipalNames, UserPrincipalName, 'msDS-KeyVersionNumber'
    $serviceAccountName = "$DomainNetbios\$ServiceAccountSamAccountName"
    $realm = $DomainFqdn.ToUpperInvariant()
    $expectedServiceAccountUpn = "$($ServiceAccountSamAccountName.ToLowerInvariant())@$realm"
    if ($serviceAccount.UserPrincipalName -ine $expectedServiceAccountUpn) {
        Write-Host "Setting the service account UPN to $expectedServiceAccountUpn..."
        Set-ADUser -Identity $serviceAccount -UserPrincipalName $expectedServiceAccountUpn
        $serviceAccount = Get-ADUser -Identity $ServiceAccountSamAccountName -Properties ServicePrincipalNames, UserPrincipalName, 'msDS-KeyVersionNumber'
    }
    $keyVersionNumber = $serviceAccount.'msDS-KeyVersionNumber'
    if ($null -eq $keyVersionNumber) {
        throw "Unable to read msDS-KeyVersionNumber for '$serviceAccountName'."
    }
    $sqlFqdn = "$SqlHostName.$DomainFqdn".ToLowerInvariant()
    $spns = @(
        "MSSQLSvc/$SqlHostName`:$SqlPort",
        "MSSQLSvc/$sqlFqdn`:$SqlPort",
        "MSSQLSvc/$SqlHostName",
        "MSSQLSvc/$sqlFqdn"
    )

    Write-Host "Registering SPNs for $serviceAccountName..."
    foreach ($spn in $spns) {
        if ($serviceAccount.ServicePrincipalNames -contains $spn) {
            Write-Host "SPN '$spn' is already assigned to $serviceAccountName; skipping."
            continue
        }

        & setspn.exe -S $spn $serviceAccountName
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to register SPN '$spn'. It may be assigned to a different account. Resolve duplicate SPNs before continuing."
        }
    }

    $keytabDirectory = Split-Path -Parent $KeytabPath
    New-Item -ItemType Directory -Path $keytabDirectory -Force | Out-Null
    Remove-Item -LiteralPath $KeytabPath -Force -ErrorAction SilentlyContinue

    $serviceAccountPassword = Read-Host -Prompt "Password for $serviceAccountName" -AsSecureString
    $passwordPointer = [IntPtr]::Zero
    $serviceAccountPasswordText = $null
    try {
        Write-Host "Resetting the password for $serviceAccountName to synchronize its Kerberos key..."
        Set-ADAccountPassword -Identity $serviceAccount -Reset -NewPassword $serviceAccountPassword
        $serviceAccount = Get-ADUser -Identity $ServiceAccountSamAccountName -Properties 'msDS-KeyVersionNumber'
        $keyVersionNumber = $serviceAccount.'msDS-KeyVersionNumber'
        if ($null -eq $keyVersionNumber) {
            throw "Unable to read the updated msDS-KeyVersionNumber for '$serviceAccountName'."
        }

        $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($serviceAccountPassword)
        $serviceAccountPasswordText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
        $keytabPrincipals = @(
            "MSSQLSvc/$SqlHostName`:$SqlPort@$realm",
            "MSSQLSvc/$sqlFqdn`:$SqlPort@$realm",
            "MSSQLSvc/$SqlHostName@$realm",
            "MSSQLSvc/$sqlFqdn@$realm",
            $expectedServiceAccountUpn
        )

        Write-Host "Creating keytab entries for $serviceAccountName..."
        # ktpass writes informational messages to stderr even when successful.
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            for ($principalIndex = 0; $principalIndex -lt $keytabPrincipals.Count; $principalIndex++) {
                $principal = $keytabPrincipals[$principalIndex]
                if ($principalIndex -eq 0) {
                    $ktpassOutput = & ktpass.exe -princ $principal -mapuser $serviceAccountName -pass $serviceAccountPasswordText -crypto AES256-SHA1 -ptype KRB5_NT_PRINCIPAL -kvno $keyVersionNumber -setpass -setupn -out $KeytabPath 2>&1
                }
                else {
                    $ktpassOutput = & ktpass.exe -princ $principal -mapuser $serviceAccountName -pass $serviceAccountPasswordText -crypto AES256-SHA1 -ptype KRB5_NT_PRINCIPAL -kvno $keyVersionNumber -setpass -setupn -in $KeytabPath -out $KeytabPath 2>&1
                }
                $ktpassOutput | ForEach-Object { Write-Host $_ }
                $ktpassExitCode = $LASTEXITCODE
                if ($ktpassExitCode -ne 0) {
                    throw "ktpass.exe failed to add '$principal' (exit code $ktpassExitCode)."
                }
            }
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if (-not (Test-Path -LiteralPath $KeytabPath -PathType Leaf)) {
            throw "ktpass.exe completed but did not create '$KeytabPath'."
        }
    }
    finally {
        if ($passwordPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
        }
        $serviceAccountPasswordText = $null
    }
}

if ($Stage -in 'All', 'Container') {
    Require-Command docker
    if ([string]::IsNullOrWhiteSpace($DomainControllerFqdn) -or [string]::IsNullOrWhiteSpace($DomainControllerIp)) {
        throw 'The Container stage requires -DomainControllerFqdn and -DomainControllerIp.'
    }
    if (-not (Test-Path -LiteralPath $KeytabPath -PathType Leaf)) {
        throw "Keytab '$KeytabPath' was not found. Run -Stage ActiveDirectory first and transfer the keytab to this computer."
    }

    & docker container inspect $ContainerName *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker container '$ContainerName' was not found. Start the SQL Server container first."
    }

    $dockerDnsConfiguration = & docker inspect $ContainerName --format '{{json .HostConfig.Dns}} {{json .HostConfig.DnsSearch}} {{json .HostConfig.ExtraHosts}}'
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Docker DNS settings for '$ContainerName'." }
    $dockerDnsConfigurationText = $dockerDnsConfiguration -join "`n"
    if ($dockerDnsConfigurationText -notmatch [regex]::Escape($DomainControllerIp) -or $dockerDnsConfigurationText -notmatch [regex]::Escape($DomainFqdn)) {
        throw "Container '$ContainerName' must be recreated with --dns $DomainControllerIp --dns-search $DomainFqdn. Docker DNS settings cannot be changed for an existing container."
    }
    $requiredDomainControllerHost = "$DomainControllerFqdn`:$DomainControllerIp"
    $requiredNetbiosHost = "$DomainNetbios`:$DomainControllerIp"
    $domainControllerHostIndex = $dockerDnsConfigurationText.IndexOf($requiredDomainControllerHost, [StringComparison]::OrdinalIgnoreCase)
    $netbiosHostIndex = $dockerDnsConfigurationText.IndexOf($requiredNetbiosHost, [StringComparison]::OrdinalIgnoreCase)
    if ($domainControllerHostIndex -lt 0 -or $netbiosHostIndex -lt 0 -or $domainControllerHostIndex -gt $netbiosHostIndex) {
        throw "Container '$ContainerName' must be recreated with --add-host $DomainControllerFqdn`:$DomainControllerIp before --add-host $DomainNetbios`:$DomainControllerIp. Active Directory reverse DNS must return the domain controller FQDN."
    }

    Write-Host "Copying keytab into $ContainerName..."
    & docker exec -u 0 $ContainerName mkdir -p /var/opt/mssql/secrets
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the container keytab directory.' }

    & docker cp $KeytabPath "${ContainerName}:/var/opt/mssql/secrets/mssql.keytab"
    if ($LASTEXITCODE -ne 0) { throw 'Unable to copy the keytab into the container.' }

    $mssqlIdentity = & docker exec $ContainerName id mssql
    if ($LASTEXITCODE -ne 0) { throw "Unable to read the mssql user identity in '$ContainerName'." }

    $mssqlIdentityMatch = [regex]::Match(($mssqlIdentity -join "`n"), 'uid=(?<uid>\d+).*gid=(?<gid>\d+)')
    if (-not $mssqlIdentityMatch.Success) {
        throw "Unable to parse the mssql user identity: $($mssqlIdentity -join ' ')"
    }

    $mssqlOwner = "$($mssqlIdentityMatch.Groups['uid'].Value):$($mssqlIdentityMatch.Groups['gid'].Value)"
    & docker exec -u 0 $ContainerName chown $mssqlOwner /var/opt/mssql/secrets
    if ($LASTEXITCODE -ne 0) { throw 'Unable to set ownership of the Kerberos cache directory.' }

    & docker exec -u 0 $ContainerName chmod 700 /var/opt/mssql/secrets
    if ($LASTEXITCODE -ne 0) { throw 'Unable to set permissions on the Kerberos cache directory.' }

    & docker exec -u 0 $ContainerName chown $mssqlOwner /var/opt/mssql/secrets/mssql.keytab
    if ($LASTEXITCODE -ne 0) { throw 'Unable to set keytab ownership.' }

    & docker exec -u 0 $ContainerName chmod 400 /var/opt/mssql/secrets/mssql.keytab
    if ($LASTEXITCODE -ne 0) { throw 'Unable to set keytab permissions.' }

    $realm = $DomainFqdn.ToUpperInvariant()
    $krb5Configuration = @"
[libdefaults]
    default_realm = $realm
    default_keytab_name = /var/opt/mssql/secrets/mssql.keytab
    default_ccache_name = ""
    udp_preference_limit = 0
    dns_lookup_kdc = false

[realms]
    $realm = {
        kdc = $DomainControllerIp
        admin_server = $DomainControllerFqdn
        default_domain = $DomainFqdn
    }

[domain_realm]
    .$DomainFqdn = $realm
    $DomainFqdn = $realm
"@
    $temporaryKrb5Path = [IO.Path]::GetTempFileName()
    try {
        Set-Content -LiteralPath $temporaryKrb5Path -Value $krb5Configuration -NoNewline
        & docker cp $temporaryKrb5Path "${ContainerName}:/etc/krb5.conf"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to copy krb5.conf into the container.' }
    }
    finally {
        Remove-Item -LiteralPath $temporaryKrb5Path -Force -ErrorAction SilentlyContinue
    }

    & docker exec -u 0 $ContainerName /opt/mssql/bin/mssql-conf set network.privilegedadaccount $ServiceAccountSamAccountName
    if ($LASTEXITCODE -ne 0) { throw 'Unable to configure the privileged Active Directory account.' }

    & docker exec -u 0 $ContainerName /opt/mssql/bin/mssql-conf set network.kerberoskeytabfile /var/opt/mssql/secrets/mssql.keytab
    if ($LASTEXITCODE -ne 0) { throw 'Unable to configure SQL Server to use the keytab.' }

    & docker exec -u 0 $ContainerName /opt/mssql/bin/mssql-conf set network.disablesssd true
    if ($LASTEXITCODE -ne 0) { throw 'Unable to configure SQL Server AD lookup mode.' }

    & docker exec -u 0 $ContainerName /opt/mssql/bin/mssql-conf set network.enablekdcfromkrb5conf true
    if ($LASTEXITCODE -ne 0) { throw 'Unable to configure SQL Server KDC discovery.' }

    Write-Host "Restarting $ContainerName..."
    & docker restart $ContainerName
    if ($LASTEXITCODE -ne 0) { throw 'Unable to restart the SQL Server container.' }
}

@"

Kerberos configuration is complete. After SQL Server finishes starting, connect
as a SQL sysadmin and run the following once to permit the requested domain user:

USE [master];
GO
CREATE LOGIN [$DomainUser] FROM WINDOWS;
GO

Then create a database user and grant only the needed permissions, for example:

USE [VF_AMR_RP1];
GO
CREATE USER [$DomainUser] FOR LOGIN [$DomainUser];
ALTER ROLE db_datareader ADD MEMBER [$DomainUser];
ALTER ROLE db_datawriter ADD MEMBER [$DomainUser];
GO
"@ | Write-Host