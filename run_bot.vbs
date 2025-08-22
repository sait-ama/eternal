' run_bot.vbs — тихий запуск бота
Option Explicit
Dim fso, sh, env, scriptDir, pyExe, pyFile, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
Set env = sh.Environment("Process")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

pyExe  = "C:\Users\User\AppData\Local\Programs\Python\Python310\pythonw.exe"
pyFile = "bot.py"

' рабочая папка = папка скрипта (чтобы логи/аватарки писались сюда)
sh.CurrentDirectory = scriptDir

' === ВАЖНО: укажи папку, где ЛЕЖАТ JSON ===
env("REMANGA_DATA_DIR") = "C:\Users\User\Desktop\Remanga\EW"
env("HISTORY_EW_FILE")  = "C:\Users\User\Desktop\Remanga\EW\history_ew.json"
env("HISTORY_ED_FILE")  = "C:\Users\User\Desktop\Remanga\EW\history_ed.json"
env("TOP10_FILE")       = "C:\Users\User\Desktop\Remanga\EW\top10.json"

' прочие переменные
env("BOT_TOKEN")   = "8380517379:AAF1pCJKN2uz2YL86yw_wKcFHGy_oFmvOjQ"
env("WEBAPP_URL")  = "https://sait-ama.github.io/eternal/index.html"
env("GITHUB_TOKEN") = "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"
env("GITHUB_REPO")  = "sait-ama/eternal"
env("GITHUB_BRANCH") = "main"

cmd = """" & pyExe & """ """ & scriptDir & "\" & pyFile & """"
sh.Run cmd, 0, False
