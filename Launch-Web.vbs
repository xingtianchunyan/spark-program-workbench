Option Explicit

Dim shell, fso, folder, batchPath, logPath, markerPath, runtimePath
Dim nonce, url, runtimeNonce, runtimeVersion, stream, line, key, value, i, ready

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
batchPath = folder & "\Launch-Web.bat"
logPath = folder & "\launch-web.log"
markerPath = folder & "\launch-web.failed"
runtimePath = folder & "\launch-web.runtime"
Randomize
nonce = Replace(CStr(Timer), ".", "") & "-" & CStr(Int(Rnd * 1000000))
shell.CurrentDirectory = folder
If fso.FileExists(markerPath) Then fso.DeleteFile markerPath, True
If fso.FileExists(runtimePath) Then fso.DeleteFile runtimePath, True
shell.Run "cmd.exe /d /c call """ & batchPath & """ --silent """ & nonce & """", 0, False

' The runtime file is written only after the current server has bound its port.
' Its per-launch nonce prevents an older process from being accepted.  Opening
' the browser here avoids depending on optional Windows XMLHTTP components.
ready = False
For i = 1 To 30
    WScript.Sleep 500
    url = ""
    runtimeNonce = ""
    runtimeVersion = ""
    If fso.FileExists(runtimePath) Then
        Set stream = fso.OpenTextFile(runtimePath, 1, False)
        Do Until stream.AtEndOfStream
            line = stream.ReadLine
            If InStr(line, "=") > 0 Then
                key = LCase(Trim(Left(line, InStr(line, "=") - 1)))
                value = Trim(Mid(line, InStr(line, "=") + 1))
                If key = "url" Then url = value
                If key = "nonce" Then runtimeNonce = value
                If key = "version" Then runtimeVersion = value
            End If
        Loop
        stream.Close
    End If
    If url <> "" And runtimeNonce = nonce And runtimeVersion = "2.6.1" Then
        ready = True
    End If
    If ready Then Exit For
    If fso.FileExists(markerPath) Then Exit For
Next

If ready Then
    shell.Run url, 1, False
    WScript.Quit 0
End If

MsgBox "本地服务未能启动。完整原因已写入：" & vbCrLf & logPath, _
       vbCritical, "Spark Program Workbench"
If fso.FileExists(logPath) Then
    shell.Run "notepad.exe """ & logPath & """", 1, False
End If
WScript.Quit 1
