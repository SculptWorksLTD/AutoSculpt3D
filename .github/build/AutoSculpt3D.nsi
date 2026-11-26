; AutoSculpt 3D - Windows installer (NSIS 3, zlib licence: commercial use allowed)
; (c) 2026 Enrico Migliorati. All rights reserved.
; Build with:  makensis /INPUTCHARSET UTF8 /DVERSION=x.y.z installer\windows\AutoSculpt3D.nsi   (after tools\build_windows.py)
; Languages: English, Italian, German, French, Spanish, Brazilian Portuguese, Japanese, chosen from the Windows
; language (a dialog lets the user change it).
; To add a language: one more MUI_LANGUAGE, its LicenseLangString (build_windows.py writes license_<code>.txt
; from installer/<code>.lproj/license.html) and a LangString for every message below.

Unicode true
ManifestDPIAware true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!define APPNAME "AutoSculpt 3D"
!define STAGE "..\..\build\win\stage"
!define EXE "$INSTDIR\python\AutoSculpt3D.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\AutoSculpt3D"
!define WEBVIEW2_ID "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

Name "${APPNAME}"
OutFile "..\..\download\AutoSculpt3D-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\AutoSculpt 3D"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
BrandingText "AutoSculpt 3D ${VERSION} · © 2026 Enrico Migliorati"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=1033 "FileDescription" "${APPNAME} setup - From dream to reality"
VIAddVersionKey /LANG=1033 "Comments" "From dream to reality"
VIAddVersionKey /LANG=1033 "CompanyName" "Enrico Migliorati"
VIAddVersionKey /LANG=1033 "LegalCopyright" "© 2026 Enrico Migliorati. All rights reserved."
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

!define MUI_ICON "..\..\build\win\AutoSculpt3D.ico"
!define MUI_UNICON "..\..\build\win\AutoSculpt3D.ico"
!define MUI_ABORTWARNING
!define MUI_LANGDLL_ALLLANGUAGES
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "Software\AutoSculpt3D"
!define MUI_LANGDLL_REGISTRY_VALUENAME "InstallerLanguage"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "$(MsgRun)"
!define MUI_FINISHPAGE_RUN_FUNCTION StartApp
!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_TEXT "$(MsgDesktop)"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION DesktopShortcut

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "$(LicenseFile)"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Italian"
!insertmacro MUI_LANGUAGE "German"
!insertmacro MUI_LANGUAGE "French"
!insertmacro MUI_LANGUAGE "Spanish"
!insertmacro MUI_LANGUAGE "PortugueseBR"
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_RESERVEFILE_LANGDLL

LicenseLangString LicenseFile ${LANG_ENGLISH} "..\..\build\win\license_en.txt"
LicenseLangString LicenseFile ${LANG_ITALIAN} "..\..\build\win\license_it.txt"
LicenseLangString LicenseFile ${LANG_GERMAN} "..\..\build\win\license_de.txt"
LicenseLangString LicenseFile ${LANG_FRENCH} "..\..\build\win\license_fr.txt"
LicenseLangString LicenseFile ${LANG_SPANISH} "..\..\build\win\license_es.txt"
LicenseLangString LicenseFile ${LANG_PORTUGUESEBR} "..\..\build\win\license_pt_BR.txt"
LicenseLangString LicenseFile ${LANG_JAPANESE} "..\..\build\win\license_ja.txt"

LangString MsgRun ${LANG_ENGLISH} "Start AutoSculpt 3D"
LangString MsgRun ${LANG_ITALIAN} "Avvia AutoSculpt 3D"
LangString MsgRun ${LANG_GERMAN} "AutoSculpt 3D starten"
LangString MsgRun ${LANG_FRENCH} "Lancer AutoSculpt 3D"
LangString MsgRun ${LANG_SPANISH} "Iniciar AutoSculpt 3D"
LangString MsgRun ${LANG_PORTUGUESEBR} "Abrir o AutoSculpt 3D"
LangString MsgRun ${LANG_JAPANESE} "AutoSculpt 3D を起動"
LangString MsgDesktop ${LANG_ENGLISH} "Create a desktop shortcut"
LangString MsgDesktop ${LANG_ITALIAN} "Crea un collegamento sul desktop"
LangString MsgDesktop ${LANG_GERMAN} "Verknüpfung auf dem Desktop erstellen"
LangString MsgDesktop ${LANG_FRENCH} "Créer un raccourci sur le bureau"
LangString MsgDesktop ${LANG_SPANISH} "Crear un acceso directo en el escritorio"
LangString MsgDesktop ${LANG_PORTUGUESEBR} "Criar um atalho na área de trabalho"
LangString MsgDesktop ${LANG_JAPANESE} "デスクトップにショートカットを作成"
LangString Msg64 ${LANG_ENGLISH} "AutoSculpt 3D requires 64-bit Windows."
LangString Msg64 ${LANG_ITALIAN} "AutoSculpt 3D richiede Windows a 64 bit."
LangString Msg64 ${LANG_GERMAN} "AutoSculpt 3D erfordert ein 64-Bit-Windows."
LangString Msg64 ${LANG_FRENCH} "AutoSculpt 3D nécessite Windows 64 bits."
LangString Msg64 ${LANG_SPANISH} "AutoSculpt 3D necesita Windows de 64 bits."
LangString Msg64 ${LANG_PORTUGUESEBR} "O AutoSculpt 3D requer o Windows de 64 bits."
LangString Msg64 ${LANG_JAPANESE} "AutoSculpt 3D には 64 ビット版 Windows が必要です。"
LangString MsgClose ${LANG_ENGLISH} "AutoSculpt 3D is open and will be closed to continue.$\n$\nSave your work, then press OK."
LangString MsgClose ${LANG_ITALIAN} "AutoSculpt 3D è aperto e verrà chiuso per continuare.$\n$\nSalva il lavoro, poi premi OK."
LangString MsgClose ${LANG_GERMAN} "AutoSculpt 3D ist geöffnet und wird zum Fortfahren geschlossen.$\n$\nSpeichere deine Arbeit und klicke dann auf OK."
LangString MsgClose ${LANG_FRENCH} "AutoSculpt 3D est ouvert et va être fermé pour continuer.$\n$\nEnregistrez votre travail, puis cliquez sur OK."
LangString MsgClose ${LANG_SPANISH} "AutoSculpt 3D está abierto y se cerrará para continuar.$\n$\nGuarda tu trabajo y pulsa Aceptar."
LangString MsgClose ${LANG_PORTUGUESEBR} "O AutoSculpt 3D está aberto e será fechado para continuar.$\n$\nSalve seu trabalho e clique em OK."
LangString MsgClose ${LANG_JAPANESE} "AutoSculpt 3D が開いているため、続行するには終了します。$\n$\n作業を保存してから OK を押してください。"
LangString MsgWebView ${LANG_ENGLISH} "To show its window, AutoSculpt 3D uses Microsoft Edge WebView2, which does not seem to be installed on this computer.$\n$\nIt is usually already present on Windows 10 and 11. Do you want to open Microsoft's page to download it (free)?"
LangString MsgWebView ${LANG_ITALIAN} "Per mostrare la finestra, AutoSculpt 3D usa Microsoft Edge WebView2, che su questo computer non risulta installato.$\n$\nDi solito è già presente su Windows 10 e 11. Vuoi aprire la pagina di Microsoft per scaricarlo (gratuito)?"
LangString MsgWebView ${LANG_GERMAN} "Zum Anzeigen des Fensters verwendet AutoSculpt 3D Microsoft Edge WebView2, das auf diesem Computer anscheinend nicht installiert ist.$\n$\nUnter Windows 10 und 11 ist es meist schon vorhanden. Möchtest du die Microsoft-Seite zum (kostenlosen) Herunterladen öffnen?"
LangString MsgWebView ${LANG_FRENCH} "Pour afficher sa fenêtre, AutoSculpt 3D utilise Microsoft Edge WebView2, qui ne semble pas installé sur cet ordinateur.$\n$\nIl est généralement déjà présent sous Windows 10 et 11. Voulez-vous ouvrir la page de Microsoft pour le télécharger (gratuit) ?"
LangString MsgWebView ${LANG_SPANISH} "Para mostrar su ventana, AutoSculpt 3D usa Microsoft Edge WebView2, que no parece estar instalado en este ordenador.$\n$\nNormalmente ya viene con Windows 10 y 11. ¿Quieres abrir la página de Microsoft para descargarlo (gratis)?"
LangString MsgWebView ${LANG_PORTUGUESEBR} "Para mostrar sua janela, o AutoSculpt 3D usa o Microsoft Edge WebView2, que parece não estar instalado neste computador.$\n$\nGeralmente ele já vem no Windows 10 e 11. Quer abrir a página da Microsoft para baixá-lo (grátis)?"
LangString MsgWebView ${LANG_JAPANESE} "AutoSculpt 3D はウィンドウの表示に Microsoft Edge WebView2 を使いますが、このコンピュータにはインストールされていないようです。$\n$\n通常は Windows 10 と 11 に含まれています。ダウンロード用の Microsoft のページ（無料）を開きますか？"
LangString MsgTagline ${LANG_ENGLISH} "From 3D model to sheet-metal sculpture"
LangString MsgTagline ${LANG_ITALIAN} "Dal modello 3D alla scultura in lamiera"
LangString MsgTagline ${LANG_GERMAN} "Vom 3D-Modell zur Blechskulptur"
LangString MsgTagline ${LANG_FRENCH} "Du modèle 3D à la sculpture en tôle"
LangString MsgTagline ${LANG_SPANISH} "Del modelo 3D a la escultura de chapa"
LangString MsgTagline ${LANG_PORTUGUESEBR} "Do modelo 3D à escultura em chapa"
LangString MsgTagline ${LANG_JAPANESE} "3Dモデルから板金彫刻へ"

; the uninstaller keeps a language-neutral file name
!define UNINSTALLER "$INSTDIR\Uninstall AutoSculpt 3D.exe"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "$(Msg64)" /SD IDOK
    Abort
  ${EndIf}
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Function un.onInit
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

; if the app is open it is closed (autosave has already saved the work)
!macro CloseApp
  nsExec::ExecToStack 'cmd /c tasklist /NH /FI "IMAGENAME eq AutoSculpt3D.exe" | find /I "AutoSculpt3D.exe"'
  Pop $0
  ${If} $0 == 0
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION "$(MsgClose)" /SD IDOK IDOK +2
    Abort
    nsExec::Exec 'taskkill /IM AutoSculpt3D.exe /F'
    Sleep 1000
  ${EndIf}
!macroend

Section "AutoSculpt 3D" SecMain
  SectionIn RO
  !insertmacro CloseApp
  ; a new version fully replaces program and interface (data and engines stay in %APPDATA%)
  RMDir /r "$INSTDIR\app"
  Delete "$INSTDIR\Disinstalla AutoSculpt 3D.exe"   ; uninstaller name used by version 1.0.8
  SetOutPath "$INSTDIR"
  File /r "${STAGE}\*.*"
  WriteUninstaller "${UNINSTALLER}"

  SetOutPath "$INSTDIR"
  CreateShortcut "$SMPROGRAMS\AutoSculpt 3D.lnk" "${EXE}" '"$INSTDIR\app\main.py"' "$INSTDIR\AutoSculpt3D.ico" 0 SW_SHOWNORMAL "" "$(MsgTagline)"

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Enrico Migliorati"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\AutoSculpt3D.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"${UNINSTALLER}"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"${UNINSTALLER}" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 175000

  ; .as3d projects open with a double click
  WriteRegStr HKCU "Software\Classes\.as3d" "" "AutoSculpt3D.Project"
  WriteRegStr HKCU "Software\Classes\.as3d" "Content Type" "application/x-autosculpt3d"
  WriteRegStr HKCU "Software\Classes\AutoSculpt3D.Project" "" "AutoSculpt 3D project"
  WriteRegStr HKCU "Software\Classes\AutoSculpt3D.Project\DefaultIcon" "" "$INSTDIR\AutoSculpt3DProject.ico"
  WriteRegStr HKCU "Software\Classes\AutoSculpt3D.Project\shell\open\command" "" '"${EXE}" "$INSTDIR\app\main.py" "%1"'
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'

  Call CheckWebView2
SectionEnd

Function CheckWebView2
  SetRegView 32
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2_ID}" "pv"
  ${If} $0 == ""
    ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\${WEBVIEW2_ID}" "pv"
  ${EndIf}
  SetRegView 64
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    MessageBox MB_YESNO|MB_ICONQUESTION "$(MsgWebView)" /SD IDNO IDNO +2
    ExecShell "open" "https://developer.microsoft.com/microsoft-edge/webview2/"
  ${EndIf}
FunctionEnd

Function StartApp
  SetOutPath "$INSTDIR"
  Exec '"${EXE}" "$INSTDIR\app\main.py"'
FunctionEnd

Function DesktopShortcut
  SetOutPath "$INSTDIR"
  CreateShortcut "$DESKTOP\AutoSculpt 3D.lnk" "${EXE}" '"$INSTDIR\app\main.py"' "$INSTDIR\AutoSculpt3D.ico" 0 SW_SHOWNORMAL "" "$(MsgTagline)"
FunctionEnd

Section "Uninstall"
  !insertmacro CloseApp
  Delete "$SMPROGRAMS\AutoSculpt 3D.lnk"
  Delete "$DESKTOP\AutoSculpt 3D.lnk"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\AutoSculpt3D.ico"
  Delete "${UNINSTALLER}"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\AutoSculpt3D"
  DeleteRegKey HKCU "Software\Classes\.as3d"
  DeleteRegKey HKCU "Software\Classes\AutoSculpt3D.Project"
  ; projects in Documents and engines in %APPDATA%\AutoSculpt3D are kept: delete them by hand if no longer needed
SectionEnd
