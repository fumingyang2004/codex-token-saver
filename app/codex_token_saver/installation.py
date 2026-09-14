"""Remove only installer-owned runtime files; retain telemetry and native sessions."""
import os
from pathlib import Path
import shutil
import subprocess

from . import control, webserver
from .state import StackError, read_json, write_json, safe_path


def uninstall(store):
    result=control.disable(store)
    if result['conflicts']:
        return {**result,'uninstalled':False,'note':'Resolve edited product configuration before runtime removal'}
    webserver.stop(store)
    receipt=read_json(store.root/'install-receipt.json')
    if receipt.get('product')!='codex-token-saver' or Path(receipt.get('home','')).resolve()!=store.root.resolve():
        return {**result,'uninstalled':False,'note':'Configuration removed; no matching installer receipt, runtime preserved'}
    bindir=store.root/'bin'
    if receipt.get('path_added') and os.name=='nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment',0,winreg.KEY_READ|winreg.KEY_WRITE) as key:
            try:
                previous,kind=winreg.QueryValueEx(key,'Path')
                remaining=[p for p in previous.split(';') if p.casefold().rstrip('\\')!=str(bindir).casefold()]
                winreg.SetValueEx(key,'Path',0,kind,';'.join(remaining))
            except FileNotFoundError: pass
    elif receipt.get('profile_blocks'):
        for name,block in receipt['profile_blocks'].items():
            if name not in ('.profile','.bashrc','.zshrc'): continue
            profile=Path.home()/name
            if profile.exists(): profile.write_text(profile.read_text().replace(block,''))
    targets=[store.root/name for name in ('venv','runtime','bin','engines')]
    for target in targets:
        safe_path(target)
        if target.resolve().parent!=store.root.resolve(): raise StackError('Install removal escaped owned directory')
    receipt['uninstalled']=True
    write_json(store.root/'install-receipt.json',receipt)
    if os.name=='nt':
        # One PowerShell process handles all Windows removals after this Python
        # releases its executable/DLLs. Literal, checked paths only.
        literal=lambda p:"'"+str(p).replace("'","''")+"'"
        code=f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue\n"
        code+=f"$ctsRoot = {literal(store.root.resolve())}\n"
        code+='$ctsTargets = @('+','.join(literal(p.resolve()) for p in targets)+')\n'
        code+="foreach ($ctsTarget in $ctsTargets) {\n  if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ctsTarget)) -ne $ctsRoot) { throw 'Unsafe cleanup target' }\n  for ($ctsTry=0; $ctsTry -lt 20; $ctsTry++) {\n    if (-not (Test-Path -LiteralPath $ctsTarget)) { break }\n    try { Remove-Item -LiteralPath $ctsTarget -Recurse -Force -ErrorAction Stop; break } catch { Start-Sleep -Milliseconds 500 }\n  }\n}\n"
        script=store.root/'uninstall-cleanup.ps1'
        script.write_text(code,encoding='utf-8-sig')
        subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        for target in targets:
            if target.exists(): shutil.rmtree(target)
    return {'status':'OFF','uninstalled':True,'telemetry':'preserved','note':'Owned runtime cleanup completes after this command exits; Codex sessions and projects are retained.'}
