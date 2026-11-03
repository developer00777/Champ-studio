# Champ Studio desktop - the setup window (WPF, so it stays sharp at any display scale).
# ASCII-only (see Common.ps1); glyphs are XAML character references.

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

$script:ChampWindowXaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Champ Studio" Width="664" SizeToContent="Height" WindowStartupLocation="CenterScreen"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent" ResizeMode="CanMinimize"
        FontFamily="Segoe UI" UseLayoutRounding="True" SnapsToDevicePixels="True">
  <Window.Resources>
    <Style x:Key="Base" TargetType="Button">
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="FontSize" Value="13.5"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Padding" Value="18,9"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="9" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Primary" TargetType="Button" BasedOn="{StaticResource Base}">
      <Setter Property="Background" Value="#FF6B35"/>
      <Setter Property="BorderBrush" Value="#FF6B35"/>
      <Setter Property="Foreground" Value="#16100B"/>
      <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
          <Setter Property="Background" Value="#FF8C5A"/>
          <Setter Property="BorderBrush" Value="#FF8C5A"/>
        </Trigger>
      </Style.Triggers>
    </Style>
    <Style x:Key="Secondary" TargetType="Button" BasedOn="{StaticResource Base}">
      <Setter Property="Background" Value="#1E222D"/>
      <Setter Property="BorderBrush" Value="#2A2F3D"/>
      <Setter Property="Foreground" Value="#FAFAF7"/>
      <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
          <Setter Property="Background" Value="#2A2F3D"/>
        </Trigger>
      </Style.Triggers>
    </Style>
    <Style x:Key="Link" TargetType="Button" BasedOn="{StaticResource Base}">
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Padding" Value="0,4"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="FontWeight" Value="Normal"/>
      <Setter Property="Foreground" Value="#9CA3AF"/>
      <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
          <Setter Property="Foreground" Value="#FF8C5A"/>
        </Trigger>
      </Style.Triggers>
    </Style>
    <Style x:Key="Chrome" TargetType="Button" BasedOn="{StaticResource Base}">
      <Setter Property="Width" Value="34"/>
      <Setter Property="Height" Value="30"/>
      <Setter Property="Padding" Value="0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="Foreground" Value="#9CA3AF"/>
      <Setter Property="FontFamily" Value="Segoe Fluent Icons, Segoe MDL2 Assets"/>
      <Setter Property="FontSize" Value="10"/>
      <Setter Property="FontWeight" Value="Normal"/>
      <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
          <Setter Property="Background" Value="#1E222D"/>
          <Setter Property="Foreground" Value="#FAFAF7"/>
        </Trigger>
      </Style.Triggers>
    </Style>
  </Window.Resources>
  <Grid Margin="18">
    <Border CornerRadius="16" Background="#0B0D12">
      <Border.Effect>
        <DropShadowEffect BlurRadius="22" ShadowDepth="3" Opacity="0.55" Color="#000000"/>
      </Border.Effect>
    </Border>
    <Border CornerRadius="16" BorderBrush="#2A2F3D" BorderThickness="1">
      <Border.Background>
        <RadialGradientBrush Center="0.9,-0.05" GradientOrigin="0.9,-0.05" RadiusX="0.8" RadiusY="0.9">
          <GradientStop Color="#22FF6B35" Offset="0"/>
          <GradientStop Color="#00FF6B35" Offset="1"/>
        </RadialGradientBrush>
      </Border.Background>
      <StackPanel Margin="30,18,22,26">
        <Grid x:Name="Header" Background="Transparent">
          <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
            <Grid Width="30" Height="30">
              <Rectangle RadiusX="7.5" RadiusY="7.5" Fill="#0B0D12" Stroke="#2A2F3D" StrokeThickness="1"/>
              <Ellipse Width="17" Height="17">
                <Ellipse.Fill>
                  <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
                    <GradientStop Color="#FF8C5A" Offset="0"/>
                    <GradientStop Color="#F0571E" Offset="1"/>
                  </LinearGradientBrush>
                </Ellipse.Fill>
              </Ellipse>
              <Polygon Points="12.9,10.8 12.9,19.2 20.2,15" Fill="#0B0D12"/>
            </Grid>
            <TextBlock Margin="12,0,0,1" VerticalAlignment="Center" FontSize="17" FontWeight="Bold" Foreground="#FAFAF7">
              <Run Text="Champ"/><Run Text=" Studio" Foreground="#FF6B35"/>
            </TextBlock>
          </StackPanel>
          <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center">
            <Button x:Name="MinButton" Style="{StaticResource Chrome}" Content="&#xE921;" ToolTip="Minimize"/>
            <Button x:Name="CloseButton" Style="{StaticResource Chrome}" Content="&#xE8BB;" ToolTip="Quit" Margin="2,0,0,0"/>
          </StackPanel>
        </Grid>
        <StackPanel Margin="0,0,8,0">
          <TextBlock x:Name="TitleText" Margin="0,30,0,0" FontSize="24" FontWeight="SemiBold" Foreground="#FAFAF7" TextWrapping="Wrap"/>
          <TextBlock x:Name="DetailText" Margin="0,8,0,0" FontSize="14" Foreground="#D1D5DB" TextWrapping="Wrap" MinHeight="19"/>
          <Grid x:Name="Track" Margin="0,20,0,0" Height="6" ClipToBounds="True">
            <Border CornerRadius="3" Background="#1E222D"/>
            <Border x:Name="Fill" CornerRadius="3" Background="#FF6B35" HorizontalAlignment="Left" Width="0">
              <Border.RenderTransform>
                <TranslateTransform/>
              </Border.RenderTransform>
            </Border>
          </Grid>
          <Grid Margin="0,8,0,0">
            <TextBlock x:Name="MetaText" FontSize="12" Foreground="#6B7280"/>
            <TextBlock x:Name="PercentText" FontSize="12" Foreground="#9CA3AF" HorizontalAlignment="Right"/>
          </Grid>
          <StackPanel x:Name="StepList" Margin="0,16,0,0"/>
          <Border x:Name="NoteBox" Margin="0,16,0,0" CornerRadius="10" Background="#14171F" BorderBrush="#2A2F3D" BorderThickness="1"
                  Padding="14,11" Visibility="Collapsed">
            <Grid>
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>
              <Border x:Name="NoteBar" Width="3" CornerRadius="1.5" Background="#F59E0B" Margin="0,1,12,1"/>
              <TextBox x:Name="NoteText" Grid.Column="1" IsReadOnly="True" BorderThickness="0" Background="Transparent"
                       Foreground="#D1D5DB" FontSize="13" TextWrapping="Wrap" Padding="0" Margin="-2,0,0,0"/>
            </Grid>
          </Border>
          <Grid Margin="0,22,0,0">
            <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
              <Button x:Name="DetailsButton" Style="{StaticResource Link}" Content="Show details"/>
              <Button x:Name="LogButton" Style="{StaticResource Link}" Content="Open log file" Margin="20,0,0,0"/>
            </StackPanel>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
              <Button x:Name="SecondaryButton" Style="{StaticResource Secondary}" Visibility="Collapsed"/>
              <Button x:Name="PrimaryButton" Style="{StaticResource Primary}" Margin="10,0,0,0" Visibility="Collapsed"/>
            </StackPanel>
          </Grid>
          <TextBox x:Name="LogBox" Margin="0,16,0,0" Height="230" Visibility="Collapsed" IsReadOnly="True"
                   Background="#14171F" Foreground="#C9CED6" BorderBrush="#2A2F3D" BorderThickness="1" Padding="8,6"
                   FontFamily="Cascadia Mono, Consolas" FontSize="11.5" TextWrapping="Wrap"
                   VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
            <TextBox.Resources>
              <Style TargetType="ScrollBar">
                <Setter Property="Width" Value="10"/>
                <Setter Property="Template">
                  <Setter.Value>
                    <ControlTemplate TargetType="ScrollBar">
                      <Track x:Name="PART_Track" IsDirectionReversed="True">
                        <Track.Thumb>
                          <Thumb>
                            <Thumb.Template>
                              <ControlTemplate TargetType="Thumb">
                                <Border Background="#3A4050" CornerRadius="3" Margin="2"/>
                              </ControlTemplate>
                            </Thumb.Template>
                          </Thumb>
                        </Track.Thumb>
                      </Track>
                    </ControlTemplate>
                  </Setter.Value>
                </Setter>
              </Style>
            </TextBox.Resources>
          </TextBox>
        </StackPanel>
      </StackPanel>
    </Border>
  </Grid>
</Window>
'@

$script:ChampStepXaml = @'
<Grid xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
      xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Margin="0,5">
  <Grid.ColumnDefinitions>
    <ColumnDefinition Width="28"/>
    <ColumnDefinition Width="*"/>
    <ColumnDefinition Width="Auto"/>
  </Grid.ColumnDefinitions>
  <Grid Width="16" Height="16" HorizontalAlignment="Left" VerticalAlignment="Center">
    <Ellipse x:Name="Dot" StrokeThickness="1.5"/>
    <Path x:Name="Mark" StrokeThickness="1.8" StrokeStartLineCap="Round" StrokeEndLineCap="Round" StrokeLineJoin="Round"/>
  </Grid>
  <TextBlock x:Name="Label" Grid.Column="1" FontSize="14" VerticalAlignment="Center"/>
  <TextBlock x:Name="Note" Grid.Column="2" FontSize="12.5" Foreground="#6B7280" VerticalAlignment="Center"/>
</Grid>
'@

function ConvertTo-ChampBrush {
    param([string] $Hex)
    $b = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.ColorConverter]::ConvertFromString($Hex))
    $b.Freeze()
    $b
}

$script:ChampStepLook = @{
    pending = @{ Fill = '#00000000'; Stroke = '#3A4050'; Mark = ''; MarkColor = '#0B0D12'; Text = '#6B7280'; Weight = 'Normal' }
    active  = @{ Fill = '#FF6B35'; Stroke = '#FF6B35'; Mark = ''; MarkColor = '#0B0D12'; Text = '#FAFAF7'; Weight = 'SemiBold' }
    done    = @{ Fill = '#10B981'; Stroke = '#10B981'; Mark = 'M4.4,8.3 L7,10.8 L11.6,5.5'; MarkColor = '#0B0D12'; Text = '#D1D5DB'; Weight = 'Normal' }
    error   = @{ Fill = '#EF4444'; Stroke = '#EF4444'; Mark = 'M5.6,5.6 L10.4,10.4 M10.4,5.6 L5.6,10.4'; MarkColor = '#FAFAF7'; Text = '#FAFAF7'; Weight = 'SemiBold' }
    paused  = @{ Fill = '#3B82F6'; Stroke = '#3B82F6'; Mark = 'M6.4,5.2 L6.4,10.8 M9.6,5.2 L9.6,10.8'; MarkColor = '#FAFAF7'; Text = '#FAFAF7'; Weight = 'SemiBold' }
}

# Builds the window; returns a hashtable of the named elements plus the step rows.
function New-ChampWindow {
    param([Parameter(Mandatory)] [System.Collections.IDictionary] $Steps, [string] $IconPath)
    $w = [System.Windows.Markup.XamlReader]::Parse($script:ChampWindowXaml)
    $ui = @{ Window = $w; Rows = @{}; Shown = @{}; Indeterminate = $false; LogLines = 0 }
    foreach ($n in 'Header', 'MinButton', 'CloseButton', 'TitleText', 'DetailText', 'Track', 'Fill', 'MetaText', 'PercentText',
        'StepList', 'NoteBox', 'NoteBar', 'NoteText', 'DetailsButton', 'LogButton', 'SecondaryButton', 'PrimaryButton', 'LogBox') {
        $ui[$n] = $w.FindName($n)
    }
    if ($IconPath -and (Test-Path $IconPath)) {
        try { $w.Icon = [System.Windows.Media.Imaging.BitmapFrame]::Create((New-Object System.Uri $IconPath)) } catch { }
    }
    foreach ($key in $Steps.Keys) {
        $row = [System.Windows.Markup.XamlReader]::Parse($script:ChampStepXaml)
        $r = @{ Root = $row; Dot = $row.FindName('Dot'); Mark = $row.FindName('Mark'); Label = $row.FindName('Label'); Note = $row.FindName('Note'); State = '' }
        $r.Label.Text = $Steps[$key]
        [void]$ui.StepList.Children.Add($row)
        $ui.Rows[$key] = $r
        Set-ChampStepLook $r 'pending' ''
    }
    $ui.Header.Add_MouseLeftButtonDown({ try { [System.Windows.Window]::GetWindow($this).DragMove() } catch { } })
    $ui.MinButton.Add_Click({ [System.Windows.Window]::GetWindow($this).WindowState = 'Minimized' })
    $ui.DetailsButton.Add_Click({
        $box = $script:ChampUi.LogBox
        $show = $box.Visibility -ne 'Visible'
        $box.Visibility = if ($show) { 'Visible' } else { 'Collapsed' }
        $this.Content = if ($show) { 'Hide details' } else { 'Show details' }
        if ($show) { $box.ScrollToEnd() }
    })
    $ui
}

function Set-ChampStepLook {
    param($Row, [string] $State, [string] $Note)
    $look = $script:ChampStepLook[$State]
    if (-not $look) { $look = $script:ChampStepLook.pending }
    $Row.Dot.Fill = ConvertTo-ChampBrush $look.Fill
    $Row.Dot.Stroke = ConvertTo-ChampBrush $look.Stroke
    $Row.Mark.Stroke = ConvertTo-ChampBrush $look.MarkColor
    $Row.Mark.Data = if ($look.Mark) { [System.Windows.Media.Geometry]::Parse($look.Mark) } else { $null }
    $Row.Label.Foreground = ConvertTo-ChampBrush $look.Text
    $Row.Label.FontWeight = $look.Weight
    $Row.Note.Text = $Note
    $opacity = [System.Windows.UIElement]::OpacityProperty
    if ($State -eq 'active') {
        $pulse = New-Object System.Windows.Media.Animation.DoubleAnimation(1.0, 0.35, (New-Object System.Windows.Duration ([TimeSpan]::FromMilliseconds(800))))
        $pulse.AutoReverse = $true
        $pulse.RepeatBehavior = [System.Windows.Media.Animation.RepeatBehavior]::Forever
        $Row.Dot.BeginAnimation($opacity, $pulse)
    } else {
        $Row.Dot.BeginAnimation($opacity, $null)
        $Row.Dot.Opacity = 1
    }
    $Row.State = $State
}

function Set-ChampProgress {
    param($Ui, [double] $Percent)
    $width = $Ui.Track.ActualWidth
    if ($width -le 0) { return }
    $shift = $Ui.Fill.RenderTransform
    $x = [System.Windows.Media.TranslateTransform]::XProperty
    if ($Percent -lt 0) {
        if ($Ui.Indeterminate) { return }
        $Ui.Indeterminate = $true
        $Ui.Fill.BeginAnimation([System.Windows.FrameworkElement]::WidthProperty, $null)
        $Ui.Fill.Width = $width * 0.28
        $slide = New-Object System.Windows.Media.Animation.DoubleAnimation((-$width * 0.28), $width, (New-Object System.Windows.Duration ([TimeSpan]::FromMilliseconds(1500))))
        $slide.RepeatBehavior = [System.Windows.Media.Animation.RepeatBehavior]::Forever
        $shift.BeginAnimation($x, $slide)
        return
    }
    if ($Ui.Indeterminate) {
        $Ui.Indeterminate = $false
        $shift.BeginAnimation($x, $null)
        $shift.X = 0
        $Ui.Fill.Width = 0
    }
    $target = $width * [Math]::Min(100, $Percent) / 100
    $grow = New-Object System.Windows.Media.Animation.DoubleAnimation($target, (New-Object System.Windows.Duration ([TimeSpan]::FromMilliseconds(350))))
    $Ui.Fill.BeginAnimation([System.Windows.FrameworkElement]::WidthProperty, $grow)
}

function Set-ChampNote {
    param($Ui, [string] $Text, [string] $Color)
    if (-not $Text) { $Ui.NoteBox.Visibility = 'Collapsed'; return }
    $Ui.NoteText.Text = $Text
    $Ui.NoteBar.Background = ConvertTo-ChampBrush $Color
    $Ui.NoteBox.Visibility = 'Visible'
}

function Format-ChampElapsed {
    param([TimeSpan] $Span)
    '{0}:{1:00}' -f [int][Math]::Floor($Span.TotalMinutes), $Span.Seconds
}

# Copies the shared state into the window. Cheap enough to run several times a second.
function Update-ChampWindow {
    param($Ui, $Sync)
    $line = $null
    $added = New-Object System.Text.StringBuilder
    while ($Sync.Log.TryDequeue([ref]$line)) { [void]$added.AppendLine($line); $Ui.LogLines++ }
    if ($added.Length) {
        if ($Ui.LogLines -gt 4000) { $Ui.LogBox.Text = ($Ui.LogBox.Text -split "`r?`n" | Select-Object -Last 2500) -join "`r`n"; $Ui.LogLines = 2500 }
        $Ui.LogBox.AppendText($added.ToString())
        if ($Ui.LogBox.Visibility -eq 'Visible') { $Ui.LogBox.ScrollToEnd() }
    }
    if ($Ui.Shown.Title -ne $Sync.Title) { $Ui.TitleText.Text = $Sync.Title; $Ui.Shown.Title = $Sync.Title }
    $detail = if ($Sync.State -eq 'working') { $Sync.Detail } else { '' }
    if ($Ui.Shown.Detail -ne $detail) {
        $Ui.DetailText.Text = $detail
        $Ui.DetailText.Visibility = if ($detail) { 'Visible' } else { 'Collapsed' }
        $Ui.Shown.Detail = $detail
    }
    foreach ($key in $Ui.Rows.Keys) {
        $st = $Sync.Steps[$key]
        $sig = "$($st.State)|$($st.Note)"
        if ($Ui.Shown["step-$key"] -ne $sig) { Set-ChampStepLook $Ui.Rows[$key] $st.State $st.Note; $Ui.Shown["step-$key"] = $sig }
    }
    $working = $Sync.State -eq 'working'
    $pct = if ($working) { [double]$Sync.Percent } else { 0 }
    if ($Ui.Track.ActualWidth -gt 0 -and ($Ui.Shown.Percent -ne $pct -or ($pct -lt 0 -and -not $Ui.Indeterminate))) {
        Set-ChampProgress $Ui $pct
        $Ui.Shown.Percent = $pct
    }
    $Ui.Track.Visibility = if ($working) { 'Visible' } else { 'Collapsed' }
    $Ui.PercentText.Text = if ($working -and $pct -ge 0) { '{0:0}%' -f $pct } else { '' }
    $elapsed = [DateTime]::Now - $Sync.PhaseStarted
    $Ui.MetaText.Text = if ($working -and $elapsed.TotalSeconds -ge 4) { "$(Format-ChampElapsed $elapsed) elapsed" } else { '' }

    $note = switch ($Sync.State) {
        'working' { @($Sync.Hint, '#F59E0B') }
        'error'   { @($Sync.Error, '#EF4444') }
        'reboot'  { @($Sync.Error, '#3B82F6') }
        default   { @('', '') }
    }
    $sig = "$($Sync.State)|$($note[0])"
    if ($Ui.Shown.Note -ne $sig) { Set-ChampNote $Ui $note[0] $note[1]; $Ui.Shown.Note = $sig }

    $buttons = switch ($Sync.State) {
        'error' {
            switch ($Sync.Action) {
                'get-docker'  { @('Try again', 'Get Docker Desktop') }
                'open-docker' { @('Open Docker Desktop', 'Try again') }
                default       { @('Quit', 'Try again') }
            }
        }
        'reboot' { @('Later', 'Restart now') }
        default  { @('', '') }
    }
    $sig = $buttons -join '|'
    if ($Ui.Shown.Buttons -ne $sig) {
        foreach ($pair in @(@($Ui.SecondaryButton, $buttons[0]), @($Ui.PrimaryButton, $buttons[1]))) {
            $pair[0].Content = $pair[1]
            $pair[0].Visibility = if ($pair[1]) { 'Visible' } else { 'Collapsed' }
        }
        $Ui.Shown.Buttons = $sig
    }
}
