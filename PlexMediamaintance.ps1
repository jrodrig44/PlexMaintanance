# ============================================
# Tautulli Media Dashboard
# Scan the library, summarize watched vs unwatched media, preview deletions, and optionally delete unwatched files.
# ============================================

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$TautulliURL = "http://PlexServer:8181"
$APIKey      = $env:TAUTULLI_API_KEY
$LibraryID   = "1"  # Change to your library section ID

function Normalize-TautulliBaseUrl {
    param([string]$InputUrl)

    $value = [string]$InputUrl
    $value = $value.Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw [System.Exception]::new("Tautulli URL cannot be empty.")
    }

    if ($value -notmatch '^https?://') {
        if ($value -match ':\d+$') {
            $value = "http://$value"
        }
        else {
            $value = "http://$value:8181"
        }
    }

    return $value.TrimEnd('/')
}

function Test-TautulliConnection {
    param(
        [string]$BaseUrl,
        [string]$ApiKey
    )

    $healthUrl = "$BaseUrl/api/v2?apikey=$ApiKey&cmd=get_server_info"
    try {
        Invoke-RestMethod -Uri $healthUrl -Method GET | Out-Null
        return $true
    }
    catch {
        throw [System.Exception]::new("Cannot connect to Tautulli at '$BaseUrl'. Verify the URL, port, and that Tautulli is running.")
    }
}

function New-MediaResultsTable {
    $table = New-Object System.Data.DataTable

    [void]$table.Columns.Add("Title", [string])
    [void]$table.Columns.Add("FilePath", [string])
    [void]$table.Columns.Add("TotalPlays", [int])
    [void]$table.Columns.Add("Status", [string])

    return $table
}

function Escape-RowFilterValue {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    return $Value.Replace("'", "''").Replace("[", "[[]").Replace("]", "[]]").Replace("%", "[%]").Replace("*", "[*]")
}

function Update-MediaGridView {
    param(
        [System.Data.DataView]$View,
        [System.Windows.Forms.ComboBox]$StatusFilter,
        [System.Windows.Forms.TextBox]$SearchBox,
        [System.Windows.Forms.ComboBox]$SortBox
    )

    $filters = New-Object System.Collections.Generic.List[string]

    $searchText = ([string]$SearchBox.Text).Trim()
    if (-not [string]::IsNullOrWhiteSpace($searchText)) {
        $escapedSearch = Escape-RowFilterValue $searchText
        $filters.Add("(Title LIKE '%$escapedSearch%' OR FilePath LIKE '%$escapedSearch%' OR Status LIKE '%$escapedSearch%')")
    }

    $selectedStatus = $StatusFilter.SelectedItem
    if ($selectedStatus -and $selectedStatus.ToString() -ne "All") {
        $escapedStatus = Escape-RowFilterValue $selectedStatus.ToString()
        $filters.Add("Status = '$escapedStatus'")
    }

    if ($View) {
        $View.RowFilter = if ($filters.Count -gt 0) { $filters -join " AND " } else { "" }
    }

    $selectedSort = if ($SortBox -and $null -ne $SortBox.SelectedItem) { $SortBox.SelectedItem.ToString() } else { "Title A-Z" }

    switch ($selectedSort) {
        "Title A-Z" { if ($View) { $View.Sort = "Title ASC" } }
        "Title Z-A" { if ($View) { $View.Sort = "Title DESC" } }
        "Plays High-Low" { if ($View) { $View.Sort = "TotalPlays DESC, Title ASC" } }
        "Plays Low-High" { if ($View) { $View.Sort = "TotalPlays ASC, Title ASC" } }
        "Status" { if ($View) { $View.Sort = "Status ASC, Title ASC" } }
        default { if ($View) { $View.Sort = "Title ASC" } }
    }
}

function Get-MediaDashboardScan {
    param(
        [string]$BaseUrl,
        [string]$ApiKey,
        [string]$SectionId,
        [bool]$DeleteUnwatched,
        [bool]$PreviewDelete,
        [scriptblock]$OnProgress
    )

    $allItemsUrl = "$BaseUrl/api/v2?apikey=$ApiKey&cmd=get_library_media_info&section_id=$SectionId&length=-1"
    $allItems = (Invoke-RestMethod -Uri $allItemsUrl -Method GET).response.data.data

    $results = New-MediaResultsTable
    $deleteQueue = New-Object System.Collections.Generic.List[object]
    $totalMedia = if ($allItems) { $allItems.Count } else { 0 }
    $scannedMedia = 0
    $playedMedia = 0
    $unwatchedMedia = 0
    $deletedDuringScan = 0

    for ($index = 0; $index -lt $totalMedia; $index++) {
        $item = $allItems[$index]
        $ratingKey = $item.rating_key

        try {
            $metaUrl = "$BaseUrl/api/v2?apikey=$ApiKey&cmd=get_metadata&rating_key=$ratingKey"
            $meta = (Invoke-RestMethod -Uri $metaUrl -Method GET).response.data

            $title = $meta.title
            $file = $meta.file

            if ([string]::IsNullOrWhiteSpace($file) -and $meta.media_info -and $meta.media_info[0].parts -and $meta.media_info[0].parts[0].file) {
                $file = $meta.media_info[0].parts[0].file
            }

            if ([string]::IsNullOrWhiteSpace($file)) {
                [void]$results.Rows.Add($title, [string]::Empty, 0, "Skipped: missing file path")
            }
            else {
                $historyUrl = "$BaseUrl/api/v2?apikey=$ApiKey&cmd=get_history&rating_key=$ratingKey"
                $historyResponse = (Invoke-RestMethod -Uri $historyUrl -Method GET).response.data
                $totalPlays = if ($historyResponse.data) { $historyResponse.data.Count } else { 0 }

                $scannedMedia++

                if ($totalPlays -gt 0) {
                    $playedMedia++
                    [void]$results.Rows.Add($title, $file, $totalPlays, "Played")
                }
                else {
                    $unwatchedMedia++

                    if ($DeleteUnwatched -and $PreviewDelete) {
                        $rowIndex = $results.Rows.Count
                        [void]$results.Rows.Add($title, $file, $totalPlays, "Queued for delete")
                        [void]$deleteQueue.Add([PSCustomObject]@{
                            RowIndex = $rowIndex
                            Title    = $title
                            FilePath = $file
                        })
                    }
                    elseif ($DeleteUnwatched -and (Test-Path $file)) {
                        Remove-Item $file -Force
                        $deletedDuringScan++
                        [void]$results.Rows.Add($title, $file, $totalPlays, "Deleted")
                    }
                    elseif ($DeleteUnwatched) {
                        [void]$results.Rows.Add($title, $file, $totalPlays, "Unwatched")
                    }
                    else {
                        [void]$results.Rows.Add($title, $file, $totalPlays, "Unwatched")
                    }
                }
            }
        }
        catch {
            [void]$results.Rows.Add(($item.title), [string]::Empty, 0, "Error: Tautulli request failed. Check connectivity and API credentials.")
        }

        if ($OnProgress) {
            & $OnProgress ($index + 1) $totalMedia $scannedMedia $playedMedia $unwatchedMedia $deleteQueue.Count
        }
    }

    [PSCustomObject]@{
        TotalMedia   = $totalMedia
        ScannedMedia = $scannedMedia
        PlayedMedia  = $playedMedia
        Unwatched    = $unwatchedMedia
        Deleted      = $deletedDuringScan
        DeleteQueue  = $deleteQueue
        Results      = $results
    }
}

function Confirm-And-DeletePreviewedFiles {
    param(
        [System.Collections.Generic.List[object]]$DeleteQueue,
        [System.Data.DataTable]$ResultsTable
    )

    $deletedCount = 0
    $failedCount = 0

    foreach ($entry in $DeleteQueue) {
        $status = "Deleted"

        try {
            if (Test-Path $entry.FilePath) {
                Remove-Item $entry.FilePath -Force
            }
            else {
                $status = "Not found"
            }
        }
        catch {
            $status = "Delete failed"
            $failedCount++
        }

        if ($status -eq "Deleted") {
            $deletedCount++
        }

        if ($entry.RowIndex -lt $ResultsTable.Rows.Count) {
            $ResultsTable.Rows[$entry.RowIndex].Status = $status
        }
    }

    return [PSCustomObject]@{
        Deleted = $deletedCount
        Failed  = $failedCount
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Tautulli Media Dashboard"
$form.StartPosition = "CenterScreen"
$form.Size = New-Object System.Drawing.Size(1360, 860)
$form.MinimumSize = New-Object System.Drawing.Size(1200, 760)
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$headerPanel = New-Object System.Windows.Forms.Panel
$headerPanel.Dock = 'Top'
$headerPanel.Height = 145
$headerPanel.Padding = New-Object System.Windows.Forms.Padding(20, 15, 20, 10)

$headerLabel = New-Object System.Windows.Forms.Label
$headerLabel.Text = "Plex / Tautulli Media Dashboard"
$headerLabel.Font = New-Object System.Drawing.Font("Segoe UI", 18, [System.Drawing.FontStyle]::Bold)
$headerLabel.AutoSize = $true
$headerLabel.Location = New-Object System.Drawing.Point(20, 10)

$subHeaderLabel = New-Object System.Windows.Forms.Label
$subHeaderLabel.Text = "Scan media, filter results, preview deletions, and delete only when you confirm it."
$subHeaderLabel.AutoSize = $true
$subHeaderLabel.Location = New-Object System.Drawing.Point(22, 48)

$urlLabel = New-Object System.Windows.Forms.Label
$urlLabel.Text = "Tautulli URL"
$urlLabel.AutoSize = $true
$urlLabel.Location = New-Object System.Drawing.Point(22, 68)

$urlBox = New-Object System.Windows.Forms.TextBox
$urlBox.Text = $TautulliURL
$urlBox.Size = New-Object System.Drawing.Size(320, 23)
$urlBox.Location = New-Object System.Drawing.Point(110, 64)

$scanButton = New-Object System.Windows.Forms.Button
$scanButton.Text = "Scan Library"
$scanButton.Size = New-Object System.Drawing.Size(130, 34)
$scanButton.Location = New-Object System.Drawing.Point(450, 58)

$deleteToggle = New-Object System.Windows.Forms.CheckBox
$deleteToggle.Text = "Delete unwatched files"
$deleteToggle.AutoSize = $true
$deleteToggle.Location = New-Object System.Drawing.Point(600, 66)
$deleteToggle.Checked = $false

$previewToggle = New-Object System.Windows.Forms.CheckBox
$previewToggle.Text = "Preview deletions before removing"
$previewToggle.AutoSize = $true
$previewToggle.Location = New-Object System.Drawing.Point(810, 66)
$previewToggle.Checked = $true

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = "Ready"
$statusLabel.AutoSize = $true
$statusLabel.Location = New-Object System.Drawing.Point(22, 110)

$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Location = New-Object System.Drawing.Point(180, 105)
$progressBar.Size = New-Object System.Drawing.Size(680, 18)
$progressBar.Minimum = 0
$progressBar.Maximum = 100

$progressText = New-Object System.Windows.Forms.Label
$progressText.Text = "0 / 0"
$progressText.AutoSize = $true
$progressText.Location = New-Object System.Drawing.Point(875, 104)

$statsPanel = New-Object System.Windows.Forms.Panel
$statsPanel.Dock = 'Top'
$statsPanel.Height = 88
$statsPanel.Padding = New-Object System.Windows.Forms.Padding(20, 0, 20, 10)

$totalLabel = New-Object System.Windows.Forms.Label
$totalLabel.Text = "Total media: 0"
$totalLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$totalLabel.AutoSize = $true
$totalLabel.Location = New-Object System.Drawing.Point(22, 18)

$scannedLabel = New-Object System.Windows.Forms.Label
$scannedLabel.Text = "Scanned media: 0"
$scannedLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$scannedLabel.AutoSize = $true
$scannedLabel.Location = New-Object System.Drawing.Point(260, 18)

$playedLabel = New-Object System.Windows.Forms.Label
$playedLabel.Text = "Scanned media that has been played: 0"
$playedLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$playedLabel.AutoSize = $true
$playedLabel.Location = New-Object System.Drawing.Point(530, 18)

$unwatchedLabel = New-Object System.Windows.Forms.Label
$unwatchedLabel.Text = "Unwatched media: 0"
$unwatchedLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$unwatchedLabel.AutoSize = $true
$unwatchedLabel.Location = New-Object System.Drawing.Point(22, 48)

$queuedLabel = New-Object System.Windows.Forms.Label
$queuedLabel.Text = "Queued for delete: 0"
$queuedLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$queuedLabel.AutoSize = $true
$queuedLabel.Location = New-Object System.Drawing.Point(260, 48)

$deletedLabel = New-Object System.Windows.Forms.Label
$deletedLabel.Text = "Deleted: 0"
$deletedLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$deletedLabel.AutoSize = $true
$deletedLabel.Location = New-Object System.Drawing.Point(530, 48)

$filterPanel = New-Object System.Windows.Forms.Panel
$filterPanel.Dock = 'Top'
$filterPanel.Height = 56
$filterPanel.Padding = New-Object System.Windows.Forms.Padding(20, 5, 20, 10)

$searchLabel = New-Object System.Windows.Forms.Label
$searchLabel.Text = "Search"
$searchLabel.AutoSize = $true
$searchLabel.Location = New-Object System.Drawing.Point(22, 12)

$searchBox = New-Object System.Windows.Forms.TextBox
$searchBox.Size = New-Object System.Drawing.Size(260, 23)
$searchBox.Location = New-Object System.Drawing.Point(75, 8)

$statusFilterLabel = New-Object System.Windows.Forms.Label
$statusFilterLabel.Text = "Status"
$statusFilterLabel.AutoSize = $true
$statusFilterLabel.Location = New-Object System.Drawing.Point(360, 12)

$statusFilter = New-Object System.Windows.Forms.ComboBox
$statusFilter.DropDownStyle = 'DropDownList'
$statusFilter.Location = New-Object System.Drawing.Point(415, 8)
$statusFilter.Size = New-Object System.Drawing.Size(180, 23)
[void]$statusFilter.Items.AddRange(@("All", "Played", "Unwatched", "Queued for delete", "Deleted", "Error", "Skipped: missing file path"))
$statusFilter.SelectedIndex = 0

$sortLabel = New-Object System.Windows.Forms.Label
$sortLabel.Text = "Sort"
$sortLabel.AutoSize = $true
$sortLabel.Location = New-Object System.Drawing.Point(620, 12)

$sortBox = New-Object System.Windows.Forms.ComboBox
$sortBox.DropDownStyle = 'DropDownList'
$sortBox.Location = New-Object System.Drawing.Point(670, 8)
$sortBox.Size = New-Object System.Drawing.Size(180, 23)
[void]$sortBox.Items.AddRange(@("Title A-Z", "Title Z-A", "Plays High-Low", "Plays Low-High", "Status"))
$sortBox.SelectedIndex = 0

$grid = New-Object System.Windows.Forms.DataGridView
$grid.Dock = 'Fill'
$grid.ReadOnly = $true
$grid.AllowUserToAddRows = $false
$grid.AllowUserToDeleteRows = $false
$grid.AllowUserToOrderColumns = $true
$grid.AutoSizeColumnsMode = 'Fill'
$grid.SelectionMode = 'FullRowSelect'
$grid.MultiSelect = $false
$grid.BackgroundColor = [System.Drawing.Color]::White
$grid.BorderStyle = 'FixedSingle'
$grid.RowHeadersVisible = $false
$grid.EnableHeadersVisualStyles = $false
$grid.ColumnHeadersDefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(43, 45, 66)
$grid.ColumnHeadersDefaultCellStyle.ForeColor = [System.Drawing.Color]::White
$grid.AlternatingRowsDefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)

$script:resultsTable = New-MediaResultsTable
$script:resultsView = $script:resultsTable.DefaultView
$grid.DataSource = $script:resultsView

$grid.Add_DataBindingComplete({
    foreach ($column in $grid.Columns) {
        $column.SortMode = [System.Windows.Forms.DataGridViewColumnSortMode]::Automatic
    }
})

$form.Controls.Add($grid)
$form.Controls.Add($filterPanel)
$form.Controls.Add($statsPanel)
$form.Controls.Add($headerPanel)

$headerPanel.Controls.AddRange(@(
    $headerLabel,
    $subHeaderLabel,
    $urlLabel,
    $urlBox,
    $scanButton,
    $deleteToggle,
    $previewToggle,
    $statusLabel,
    $progressBar,
    $progressText
))

$statsPanel.Controls.AddRange(@(
    $totalLabel,
    $scannedLabel,
    $playedLabel,
    $unwatchedLabel,
    $queuedLabel,
    $deletedLabel
))

$filterPanel.Controls.AddRange(@(
    $searchLabel,
    $searchBox,
    $statusFilterLabel,
    $statusFilter,
    $sortLabel,
    $sortBox
))

function Refresh-GridView {
    Update-MediaGridView -View $script:resultsView -StatusFilter $statusFilter -SearchBox $searchBox -SortBox $sortBox
}

$searchBox.Add_TextChanged({ Refresh-GridView })
$statusFilter.Add_SelectedIndexChanged({ Refresh-GridView })
$sortBox.Add_SelectedIndexChanged({ Refresh-GridView })

$scanButton.Add_Click({
    try {
        $TautulliURL = Normalize-TautulliBaseUrl -InputUrl $urlBox.Text
        $urlBox.Text = $TautulliURL
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Invalid URL", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
        return
    }

    $scanButton.Enabled = $false
    $deleteToggle.Enabled = $false
    $previewToggle.Enabled = $false
    $urlBox.Enabled = $false
    $searchBox.Enabled = $false
    $statusFilter.Enabled = $false
    $sortBox.Enabled = $false

    $statusLabel.Text = "Scanning..."
    $progressBar.Value = 0
    $progressText.Text = "0 / 0"

    if ($script:resultsTable) {
        $script:resultsTable.Clear()
    }
    if ($script:resultsView) {
        $script:resultsView.RowFilter = ""
        $script:resultsView.Sort = "Title ASC"
    }
    Refresh-GridView
    [System.Windows.Forms.Application]::DoEvents()

    try {
        Test-TautulliConnection -BaseUrl $TautulliURL -ApiKey $APIKey

        $scanResult = Get-MediaDashboardScan -BaseUrl $TautulliURL -ApiKey $APIKey -SectionId $LibraryID -DeleteUnwatched $deleteToggle.Checked -PreviewDelete $previewToggle.Checked -OnProgress {
            param($processed, $total, $scanned, $played, $unwatched, $queued)

            $progressBar.Maximum = [Math]::Max($total, 1)
            $progressBar.Value = [Math]::Min($processed, $progressBar.Maximum)
            $progressText.Text = "$processed / $total"
            $statusLabel.Text = "Scanning item $processed of $total"
            $scannedLabel.Text = "Scanned media: $scanned"
            $playedLabel.Text = "Scanned media that has been played: $played"
            $unwatchedLabel.Text = "Unwatched media: $unwatched"
            $queuedLabel.Text = "Queued for delete: $queued"
            [System.Windows.Forms.Application]::DoEvents()
        }

        $script:resultsTable = $scanResult.Results
        $script:resultsView = $script:resultsTable.DefaultView
        $grid.DataSource = $script:resultsView
        Refresh-GridView

        $totalLabel.Text = "Total media: $($scanResult.TotalMedia)"
        $scannedLabel.Text = "Scanned media: $($scanResult.ScannedMedia)"
        $playedLabel.Text = "Scanned media that has been played: $($scanResult.PlayedMedia)"
        $unwatchedLabel.Text = "Unwatched media: $($scanResult.Unwatched)"
        $queuedLabel.Text = "Queued for delete: $($scanResult.DeleteQueue.Count)"

        if ($deleteToggle.Checked -and $previewToggle.Checked -and $scanResult.DeleteQueue.Count -gt 0) {
            $statusLabel.Text = "Preview complete. Waiting for delete confirmation."
            $confirm = [System.Windows.Forms.MessageBox]::Show(
                "Preview found $($scanResult.DeleteQueue.Count) unwatched file(s). Delete them now?",
                "Confirm deletion",
                [System.Windows.Forms.MessageBoxButtons]::YesNo,
                [System.Windows.Forms.MessageBoxIcon]::Warning
            )

            if ($confirm -eq [System.Windows.Forms.DialogResult]::Yes) {
                $deleteResult = Confirm-And-DeletePreviewedFiles -DeleteQueue $scanResult.DeleteQueue -ResultsTable $script:resultsTable
                $deletedLabel.Text = "Deleted: $($deleteResult.Deleted)"
                $queuedLabel.Text = "Queued for delete: $($scanResult.DeleteQueue.Count)"
                $statusLabel.Text = "Delete complete. Deleted $($deleteResult.Deleted) file(s); $($deleteResult.Failed) failed."
            }
            else {
                $statusLabel.Text = "Delete canceled. Preview results remain visible."
            }

            Refresh-GridView
        }
        elseif ($deleteToggle.Checked -and -not $previewToggle.Checked) {
            $deletedLabel.Text = "Deleted: $($scanResult.Deleted)"
            $statusLabel.Text = "Scan complete. Unwatched files were deleted during the scan."
        }
        else {
            $statusLabel.Text = "Scan complete. No files were deleted."
            $deletedLabel.Text = "Deleted: 0"
        }
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show("Scan failed. Check Tautulli connectivity, API credentials, and media access.", "Scan failed", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        $statusLabel.Text = "Scan failed"
    }
    finally {
        $scanButton.Enabled = $true
        $deleteToggle.Enabled = $true
        $previewToggle.Enabled = $true
        $urlBox.Enabled = $true
        $searchBox.Enabled = $true
        $statusFilter.Enabled = $true
        $sortBox.Enabled = $true
    }
})

[void]$form.ShowDialog()
