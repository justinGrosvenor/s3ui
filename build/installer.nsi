; S3UI Windows Installer — NSIS script
; Build with: makensis /DVERSION=v0.1.0 installer.nsi

!include "MUI2.nsh"

Name "S3UI"
OutFile "S3UI-Setup-${VERSION}.exe"
InstallDir "$PROGRAMFILES64\S3UI"
RequestExecutionLevel admin

; Modern UI pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; Uninstaller pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
    SetOutPath "$INSTDIR"
    File /r "dist\S3UI\*.*"

    ; Start Menu shortcuts
    CreateDirectory "$SMPROGRAMS\S3UI"
    CreateShortCut "$SMPROGRAMS\S3UI\S3UI.lnk" "$INSTDIR\S3UI.exe"
    CreateShortCut "$SMPROGRAMS\S3UI\Uninstall.lnk" "$INSTDIR\Uninstall.exe"

    ; Desktop shortcut
    CreateShortCut "$DESKTOP\S3UI.lnk" "$INSTDIR\S3UI.exe"

    ; Uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Add/Remove Programs registry entry
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "DisplayName" "S3UI"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "DisplayIcon" "$INSTDIR\S3UI.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "DisplayVersion" "${VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "Publisher" "S3UI Contributors"
SectionEnd

Section "Uninstall"
    RMDir /r "$INSTDIR"
    RMDir /r "$SMPROGRAMS\S3UI"
    Delete "$DESKTOP\S3UI.lnk"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI"
SectionEnd
