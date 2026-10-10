; Instalador do Comparador de Contratos
; Instala por usuario (sem administrador) em %LOCALAPPDATA%\Programs.
; Dados ficam em %LOCALAPPDATA%\Comparador de Contratos e nao sao apagados na desinstalacao.

Unicode true
!include "MUI2.nsh"

!define NOME "Comparador de Contratos"
!define VERSAO "2.0.1"
!define EDITOR "Comparador de Contratos"
!define CHAVE_UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\ComparadorContratos"

Name "${NOME}"
OutFile "Instalar_Comparador_Contratos.exe"
InstallDir "$LOCALAPPDATA\Programs\Comparador de Contratos"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "${EDITOR}"

VIProductVersion "${VERSAO}.0"
VIAddVersionKey /LANG=1046 "ProductName" "${NOME}"
VIAddVersionKey /LANG=1046 "CompanyName" "${EDITOR}"
VIAddVersionKey /LANG=1046 "FileDescription" "Instalador do ${NOME}"
VIAddVersionKey /LANG=1046 "FileVersion" "${VERSAO}"
VIAddVersionKey /LANG=1046 "ProductVersion" "${VERSAO}"
VIAddVersionKey /LANG=1046 "LegalCopyright" "Uso interno ${EDITOR}"

!define MUI_ICON "stage\app\icone.ico"
!define MUI_UNICON "stage\app\icone.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Comparador de Contratos"
!define MUI_WELCOMEPAGE_TEXT "Este assistente instala o Comparador de Contratos neste computador.$\r$\n$\r$\nNão é preciso ter Python nem permissão de administrador. Tudo roda localmente: nenhum contrato sai deste computador.$\r$\n$\r$\nClique em Avançar para continuar."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION AbrirApp
!define MUI_FINISHPAGE_RUN_TEXT "Abrir o Comparador de Contratos agora"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "PortugueseBR"

Section "Programa" SecPrincipal
  SetShellVarContext current
  ; atualizacao: fecha o app se estiver aberto, substitui o programa e preserva os dados
  nsExec::Exec `powershell -NoProfile -NonInteractive -Command "Get-Process pythonw,python -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\*' } | Stop-Process -Force"`
  Sleep 800
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  SetOutPath "$INSTDIR"
  File /r "stage\runtime"
  File /r "stage\app"
  File "stage\LEIA-ME.txt"

  CreateShortCut "$DESKTOP\Comparador de Contratos.lnk" "$INSTDIR\runtime\pythonw.exe" '"$INSTDIR\app\iniciar.pyw"' "$INSTDIR\app\icone.ico" 0 SW_SHOWNORMAL "" "Comparador de Contratos"
  CreateDirectory "$SMPROGRAMS\Comparador de Contratos"
  CreateShortCut "$SMPROGRAMS\Comparador de Contratos\Comparador de Contratos.lnk" "$INSTDIR\runtime\pythonw.exe" '"$INSTDIR\app\iniciar.pyw"' "$INSTDIR\app\icone.ico" 0 SW_SHOWNORMAL "" "Comparador de Contratos"
  CreateDirectory "$LOCALAPPDATA\Comparador de Contratos\dados"
  CreateShortCut "$SMPROGRAMS\Comparador de Contratos\Pasta de dados (resultados e modelos).lnk" "$LOCALAPPDATA\Comparador de Contratos\dados"
  CreateShortCut "$SMPROGRAMS\Comparador de Contratos\Leia-me.lnk" "$INSTDIR\LEIA-ME.txt"
  CreateShortCut "$SMPROGRAMS\Comparador de Contratos\Desinstalar.lnk" "$INSTDIR\Desinstalar.exe"

  WriteUninstaller "$INSTDIR\Desinstalar.exe"
  WriteRegStr HKCU "${CHAVE_UNINST}" "DisplayName" "${NOME}"
  WriteRegStr HKCU "${CHAVE_UNINST}" "DisplayVersion" "${VERSAO}"
  WriteRegStr HKCU "${CHAVE_UNINST}" "Publisher" "${EDITOR}"
  WriteRegStr HKCU "${CHAVE_UNINST}" "DisplayIcon" "$INSTDIR\app\icone.ico"
  WriteRegStr HKCU "${CHAVE_UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${CHAVE_UNINST}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegDWORD HKCU "${CHAVE_UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${CHAVE_UNINST}" "NoRepair" 1
SectionEnd

Function AbrirApp
  ExecShell "" "$INSTDIR\runtime\pythonw.exe" '"$INSTDIR\app\iniciar.pyw"'
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$DESKTOP\Comparador de Contratos.lnk"
  RMDir /r "$SMPROGRAMS\Comparador de Contratos"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  Delete "$INSTDIR\LEIA-ME.txt"
  Delete "$INSTDIR\Desinstalar.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${CHAVE_UNINST}"
  MessageBox MB_ICONINFORMATION "Programa removido.$\r$\n$\r$\nOs contratos, modelos e resultados foram mantidos em:$\r$\n$LOCALAPPDATA\Comparador de Contratos$\r$\n$\r$\nApague essa pasta manualmente se não precisar mais deles." /SD IDOK
SectionEnd
