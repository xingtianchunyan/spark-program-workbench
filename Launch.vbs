Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)

' ========== 自动创建带图标的快捷方式 ==========
' 如果快捷方式不存在，自动创建
batPath = baseDir & "\Launch.bat"
iconPath = baseDir & "\spark_icon.ico"
lnkPath = baseDir & "\Spark Program Workbench.lnk"

If Not fso.FileExists(lnkPath) Then
    If fso.FileExists(batPath) And fso.FileExists(iconPath) Then
        Set lnk = WshShell.CreateShortcut(lnkPath)
        lnk.TargetPath = batPath
        lnk.WorkingDirectory = baseDir
        lnk.IconLocation = iconPath
        lnk.Description = "Spark Program Workbench"
        lnk.WindowStyle = 1
        lnk.Save()
    End If
End If

' ========== 启动主程序 ==========
pyPath = baseDir & "\spark_task_tracker.py"

' Try pythonw first (no console window)
exitCode = WshShell.Run("pythonw """ & pyPath & """", 1, True)
If exitCode = 0 Then WScript.Quit 0

' Try python
exitCode = WshShell.Run("python """ & pyPath & """", 1, True)
If exitCode = 0 Then WScript.Quit 0

' Try py
exitCode = WshShell.Run("py """ & pyPath & """", 1, True)
If exitCode = 0 Then WScript.Quit 0

' Try python3
exitCode = WshShell.Run("python3 """ & pyPath & """", 1, True)
If exitCode = 0 Then WScript.Quit 0

' None found
MsgBox "Python not found. Please install Python from python.org or Microsoft Store.", vbCritical, "Spark Program Workbench"
