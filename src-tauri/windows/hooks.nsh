!macro AITRADE_STOP_BACKEND
  nsExec::ExecToLog 'taskkill /F /T /IM aitrade-backend.exe'
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro AITRADE_STOP_BACKEND
!macroend

!macro NSIS_HOOK_POSTINSTALL
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro AITRADE_STOP_BACKEND
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
!macroend
