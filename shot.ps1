# Photograph the tracker's own window and nothing else.
#
# An earlier version matched any window whose title contained "poker", and
# duly photographed a browser tab and a chat client. The match is now strict:
# the process must be this program's own python, and the title must be exactly
# the one it sets. No fallback -- if it is not found, nothing is captured,
# because capturing the wrong window is worse than capturing none.
param([string]$Out = "window.png", [string]$Title = "poker_analysis")

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Shot {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
  public struct R { public int L, T, Rr, B; }
}
"@

$p = Get-Process pythonw, python, poker_analysis -ErrorAction SilentlyContinue |
     Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -eq $Title } |
     Select-Object -First 1
if (-not $p) {
  "no window titled '$Title' belonging to this program is open"
  exit 1
}
[void][Shot]::ShowWindow($p.MainWindowHandle, 9)
[void][Shot]::SetForegroundWindow($p.MainWindowHandle)
Start-Sleep -Milliseconds 800
$r = New-Object Shot+R
[void][Shot]::GetWindowRect($p.MainWindowHandle, [ref]$r)
$w = $r.Rr - $r.L; $h = $r.B - $r.T
if ($w -le 0 -or $h -le 0) { "window has no size"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"captured '$($p.MainWindowTitle)' from $($p.ProcessName)  ${w}x${h} -> $Out"
