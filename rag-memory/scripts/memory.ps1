<#
.SYNOPSIS
    RAG Memory Backend — PowerShell (SQLite FTS5)

.DESCRIPTION
    Cross-platform persistent memory with full-text search.
    Uses SQLite FTS5 for keyword-based retrieval.
    Shares the same database schema as the Python backend for interoperability.

.PARAMETER Command
    Action to perform: remember, recall, forget, list, status

.PARAMETER Content
    Text content to store (for remember) or search query (for recall/forget)

.PARAMETER Tags
    Comma-separated tags for categorization

.PARAMETER Scope
    Storage scope: project (default) or global

.PARAMETER Limit
    Maximum number of results to return

.PARAMETER Source
    Memory source identifier (default: user)

.PARAMETER AsJson
    Output results as JSON

.EXAMPLE
    pwsh memory.ps1 -Command remember -Content "Use PostgreSQL for main DB"
    pwsh memory.ps1 -Command recall -Content "database"
    pwsh memory.ps1 -Command forget -Content "old decision"
    pwsh memory.ps1 -Command list
    pwsh memory.ps1 -Command status
#>

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("remember", "recall", "forget", "list", "status")]
    [string]$Command,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Content,

    [string]$Tags = "",
    [ValidateSet("project", "global")]
    [string]$Scope = "project",
    [int]$Limit = 10,
    [string]$Source = "user",
    [switch]$AsJson
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

$DefaultConfig = @{
    storage_path        = ".claude/memory"
    global_storage_path = Join-Path $HOME ".claude" "memory"
    max_results         = 10
    auto_tag            = $true
    default_scope       = "project"
}

$AutoTagRules = @{
    architecture = @("database", "schema", "api", "design", "pattern", "structure", "microservice", "monolith")
    preference   = @("prefer", "like", "always", "never", "style", "convention")
    bug          = @("bug", "fix", "issue", "error", "crash", "workaround", "patch")
    decision     = @("decided", "chose", "because", "rationale", "trade-off", "tradeoff")
    config       = @("config", "environment", "setup", "install", "deploy", "ci/cd")
    api          = @("endpoint", "request", "response", "rest", "graphql", "webhook")
    security     = @("auth", "token", "secret", "permission", "cors", "csrf")
    performance  = @("slow", "fast", "optimize", "cache", "latency", "throughput")
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Get-StoragePath {
    param([string]$Scope)
    if ($Scope -eq "global") {
        return $DefaultConfig.global_storage_path
    }
    return $DefaultConfig.storage_path
}

function Get-AutoTags {
    param([string]$Text)
    $lower = $Text.ToLower()
    $tags = @()
    foreach ($entry in $AutoTagRules.GetEnumerator()) {
        foreach ($keyword in $entry.Value) {
            if ($lower.Contains($keyword)) {
                $tags += $entry.Key
                break
            }
        }
    }
    return ($tags -join ",")
}

function New-ShortId {
    return [guid]::NewGuid().ToString().Substring(0, 8)
}

function Get-IsoTimestamp {
    return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
}

# ---------------------------------------------------------------------------
# SQLite via System.Data.SQLite or Microsoft.Data.Sqlite
# ---------------------------------------------------------------------------

# Use the built-in SQLite support available in PowerShell/dotnet
function Initialize-Database {
    param([string]$DbPath)

    $directory = Split-Path $DbPath -Parent
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    # Try to load SQLite assembly
    $sqliteLoaded = $false

    # Method 1: Try Microsoft.Data.Sqlite (available in .NET Core / PowerShell 7+)
    try {
        Add-Type -AssemblyName "Microsoft.Data.Sqlite" -ErrorAction Stop
        $sqliteLoaded = $true
        $script:SqliteProvider = "Microsoft.Data.Sqlite"
    }
    catch { }

    # Method 2: Try System.Data.SQLite
    if (-not $sqliteLoaded) {
        try {
            Add-Type -AssemblyName "System.Data.SQLite" -ErrorAction Stop
            $sqliteLoaded = $true
            $script:SqliteProvider = "System.Data.SQLite"
        }
        catch { }
    }

    # Method 3: Use sqlite3 CLI as fallback
    if (-not $sqliteLoaded) {
        $sqlite3 = Get-Command sqlite3 -ErrorAction SilentlyContinue
        if ($sqlite3) {
            $script:SqliteProvider = "CLI"
            $sqliteLoaded = $true
        }
    }

    if (-not $sqliteLoaded) {
        Write-Error "No SQLite provider found. Install PowerShell 7+ or sqlite3 CLI."
        exit 1
    }

    # Initialize schema
    $schema = @"
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    source TEXT DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"@
    Invoke-Sql -DbPath $DbPath -Query $schema
}

function Invoke-Sql {
    param(
        [string]$DbPath,
        [string]$Query,
        [hashtable]$Parameters = @{}
    )

    if ($script:SqliteProvider -eq "CLI") {
        return Invoke-SqlCli -DbPath $DbPath -Query $Query -Parameters $Parameters
    }

    # Use .NET SQLite provider
    if ($script:SqliteProvider -eq "Microsoft.Data.Sqlite") {
        $conn = New-Object Microsoft.Data.Sqlite.SqliteConnection "Data Source=$DbPath"
    }
    else {
        $conn = New-Object System.Data.SQLite.SQLiteConnection "Data Source=$DbPath;Version=3;"
    }

    try {
        $conn.Open()
        $cmd = $conn.CreateCommand()
        $cmd.CommandText = $Query

        foreach ($entry in $Parameters.GetEnumerator()) {
            $param = $cmd.CreateParameter()
            $param.ParameterName = $entry.Key
            $param.Value = $entry.Value
            $cmd.Parameters.Add($param) | Out-Null
        }

        if ($Query.TrimStart() -match "^(SELECT|PRAGMA)") {
            $reader = $cmd.ExecuteReader()
            $results = @()
            while ($reader.Read()) {
                $row = @{}
                for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                    $row[$reader.GetName($i)] = $reader.GetValue($i)
                }
                $results += [PSCustomObject]$row
            }
            $reader.Close()
            return $results
        }
        else {
            $affected = $cmd.ExecuteNonQuery()
            return $affected
        }
    }
    finally {
        $conn.Close()
    }
}

function Invoke-SqlCli {
    param(
        [string]$DbPath,
        [string]$Query,
        [hashtable]$Parameters = @{}
    )

    # Simple parameter substitution for CLI mode
    $resolvedQuery = $Query
    foreach ($entry in $Parameters.GetEnumerator()) {
        $safeValue = $entry.Value -replace "'", "''"
        $resolvedQuery = $resolvedQuery -replace $entry.Key, "'$safeValue'"
    }

    if ($resolvedQuery.TrimStart() -match "^(SELECT|PRAGMA)") {
        $output = $resolvedQuery | sqlite3 -json $DbPath 2>$null
        if ($output) {
            try {
                return $output | ConvertFrom-Json
            }
            catch {
                return @()
            }
        }
        return @()
    }
    else {
        $resolvedQuery | sqlite3 $DbPath 2>$null
        # Return changes count via separate query
        $changesOutput = "SELECT changes();" | sqlite3 -json $DbPath 2>$null
        if ($changesOutput) {
            try {
                $changesResult = $changesOutput | ConvertFrom-Json
                return $changesResult[0].'changes()'
            }
            catch { return 0 }
        }
        return 0
    }
}

# ---------------------------------------------------------------------------
# Memory Operations
# ---------------------------------------------------------------------------

function Invoke-Remember {
    param(
        [string]$Text,
        [string]$Tags,
        [string]$Source,
        [string]$DbPath
    )

    if (-not $Tags -and $DefaultConfig.auto_tag) {
        $Tags = Get-AutoTags -Text $Text
    }

    $id = New-ShortId
    $now = Get-IsoTimestamp

    $query = "INSERT INTO memories (id, content, tags, source, created_at, updated_at) VALUES (@id, @content, @tags, @source, @created, @updated)"
    $params = @{
        "@id"      = $id
        "@content" = $Text
        "@tags"    = $Tags
        "@source"  = $Source
        "@created" = $now
        "@updated" = $now
    }

    Invoke-Sql -DbPath $DbPath -Query $query -Parameters $params | Out-Null

    $result = @{
        id         = $id
        content    = $Text
        tags       = $Tags
        created_at = $now
    }

    if ($AsJson) {
        $result | ConvertTo-Json
    }
    else {
        Write-Host "Stored memory (id: $id)"
        if ($Tags) { Write-Host "Tags: [$Tags]" } else { Write-Host "Tags: [none]" }
        Write-Host "Content: $($Text.Substring(0, [Math]::Min(200, $Text.Length)))"
    }
}

function Invoke-Recall {
    param(
        [string]$Query,
        [int]$MaxResults,
        [string]$DbPath
    )

    # Use LIKE-based search (FTS5 requires .NET provider which may not support it via CLI)
    $likePattern = "%$Query%"
    $sql = "SELECT * FROM memories WHERE content LIKE @pattern OR tags LIKE @pattern ORDER BY updated_at DESC LIMIT @limit"
    $params = @{
        "@pattern" = $likePattern
        "@limit"   = $MaxResults
    }

    $results = Invoke-Sql -DbPath $DbPath -Query $sql -Parameters $params

    if ($AsJson) {
        $results | ConvertTo-Json -Depth 5
    }
    elseif ($results -and $results.Count -gt 0) {
        Write-Host "Found $($results.Count) memories matching `"$Query`":`n"
        $i = 1
        foreach ($mem in $results) {
            $date = if ($mem.created_at) { $mem.created_at.Substring(0, 10) } else { "unknown" }
            $tagStr = if ($mem.tags) { " (tags: $($mem.tags))" } else { "" }
            Write-Host "$i. [$date]$tagStr"
            Write-Host "   $($mem.content)"
            Write-Host ""
            $i++
        }
    }
    else {
        Write-Host "No memories found matching `"$Query`"."
    }
}

function Invoke-Forget {
    param(
        [string]$Query,
        [string]$DbPath
    )

    # Try by ID first
    $deleted = Invoke-Sql -DbPath $DbPath -Query "DELETE FROM memories WHERE id = @id" -Parameters @{ "@id" = $Query }
    if ($deleted -gt 0) {
        Write-Host "Deleted $deleted memory(ies) matching `"$Query`"."
        return
    }

    # Then by content
    $deleted = Invoke-Sql -DbPath $DbPath -Query "DELETE FROM memories WHERE content LIKE @pattern" -Parameters @{ "@pattern" = "%$Query%" }
    Write-Host "Deleted $deleted memory(ies) matching `"$Query`"."
}

function Invoke-List {
    param(
        [string]$TagsFilter,
        [string]$DbPath
    )

    if ($TagsFilter) {
        $sql = "SELECT * FROM memories WHERE tags LIKE @tags ORDER BY updated_at DESC"
        $params = @{ "@tags" = "%$TagsFilter%" }
    }
    else {
        $sql = "SELECT * FROM memories ORDER BY updated_at DESC"
        $params = @{}
    }

    $results = Invoke-Sql -DbPath $DbPath -Query $sql -Parameters $params

    if ($AsJson) {
        $results | ConvertTo-Json -Depth 5
    }
    elseif ($results -and $results.Count -gt 0) {
        Write-Host "Total memories: $($results.Count)`n"
        $i = 1
        foreach ($mem in $results) {
            $date = if ($mem.created_at) { $mem.created_at.Substring(0, 10) } else { "unknown" }
            $tagStr = if ($mem.tags) { " (tags: $($mem.tags))" } else { "" }
            Write-Host "$i. [$date]$tagStr"
            Write-Host "   $($mem.content)"
            Write-Host ""
            $i++
        }
    }
    else {
        Write-Host "No memories stored yet."
    }
}

function Invoke-Status {
    param([string]$DbPath)

    $countResult = Invoke-Sql -DbPath $DbPath -Query "SELECT COUNT(*) as count FROM memories"
    $count = if ($countResult) { $countResult[0].count } else { 0 }

    $info = @{
        scope           = $Scope
        backend         = "sqlite (fts5/like) — PowerShell"
        total_memories  = $count
        vector_entries  = "n/a (use Python backend for vector search)"
        storage_path    = Get-StoragePath -Scope $Scope
        embedding_model = "n/a"
    }

    if ($AsJson) {
        $info | ConvertTo-Json
    }
    else {
        Write-Host "Memory Store Status"
        Write-Host ("=" * 40)
        foreach ($entry in $info.GetEnumerator()) {
            Write-Host "  $($entry.Key): $($entry.Value)"
        }
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

$ContentText = ($Content -join " ").Trim()
$StoragePath = Get-StoragePath -Scope $Scope
$DbPath = Join-Path $StoragePath "memories.db"

Initialize-Database -DbPath $DbPath

switch ($Command) {
    "remember" {
        if (-not $ContentText) {
            Write-Error "No content provided to remember."
            exit 1
        }
        Invoke-Remember -Text $ContentText -Tags $Tags -Source $Source -DbPath $DbPath
    }
    "recall" {
        if (-not $ContentText) {
            Write-Error "No query provided."
            exit 1
        }
        Invoke-Recall -Query $ContentText -MaxResults $Limit -DbPath $DbPath
    }
    "forget" {
        if (-not $ContentText) {
            Write-Error "No query provided."
            exit 1
        }
        Invoke-Forget -Query $ContentText -DbPath $DbPath
    }
    "list" {
        Invoke-List -TagsFilter $Tags -DbPath $DbPath
    }
    "status" {
        Invoke-Status -DbPath $DbPath
    }
}
