"""Release installer: owns one isolated venv; no user-managed Python changes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--home',type=Path)
    p.add_argument('--codex-home',type=Path,default=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex')))
    p.add_argument('--no-path',action='store_true')
    args=p.parse_args()
    if not (3,11)<=sys.version_info[:2]<(3,14):
        raise SystemExit('Use Python 3.11–3.13; the installer can provision Python 3.13 automatically.')
    root=(args.home or (Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'CodexTokenSaver' if os.name=='nt' else Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'codex-token-saver')).resolve()
    receipt=root/'install-receipt.json'
    if root.is_symlink() or (hasattr(root,'is_junction') and root.is_junction()):
        raise SystemExit('Refusing redirected install directory')
    if (root/'venv').exists() and not receipt.exists():
        raise SystemExit('Existing venv is not owned by this installer')
    root.mkdir(parents=True,exist_ok=True)
    info={'product':'codex-token-saver','home':str(root),'codex_home':str(args.codex_home.resolve()),'path_added':not args.no_path,'version':'0.1.0-beta.1'}
    receipt.write_text(json.dumps(info,indent=2),encoding='utf-8')
    venv=root/'venv'
    if not venv.exists(): subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
    python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    bundle=Path(__file__).resolve().parents[1]
    wheels=list((bundle/'wheels').glob('codex_token_saver-*.whl'))
    if len(wheels)!=1: raise SystemExit('Release must include exactly one application wheel')
    install=[str(python),'-m','pip','install','--disable-pip-version-check',str(wheels[0])+'[engine]']
    constraints=bundle/'requirements.lock'
    if constraints.exists(): install+=['-c',str(constraints)]
    subprocess.run(install,check=True)
    command=[str(python),'-m','codex_token_saver','--home',str(root),'--codex-home',str(args.codex_home.resolve())]
    subprocess.run([*command,'_setup'],check=True)
    bindir=root/'bin';bindir.mkdir(exist_ok=True)
    if os.name=='nt':
        (bindir/'codex-saver.cmd').write_text('@echo off\r\n"%~dp0..\\venv\\Scripts\\python.exe" -m codex_token_saver --home "%~dp0.." %*\r\n',encoding='utf-8',newline='')
        if not args.no_path:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment',0,winreg.KEY_READ|winreg.KEY_WRITE) as key:
                try: previous,kind=winreg.QueryValueEx(key,'Path')
                except FileNotFoundError: previous,kind='',winreg.REG_EXPAND_SZ
                if str(bindir).casefold() not in [v.casefold().rstrip('\\') for v in previous.split(';')]:
                    winreg.SetValueEx(key,'Path',0,kind,str(bindir)+';'+previous)
    else:
        script=bindir/'codex-saver'
        script.write_text('#!/bin/sh\nCTS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\nexec "$CTS_DIR/venv/bin/python" -m codex_token_saver --home "$CTS_DIR" "$@"\n',encoding='utf-8')
        script.chmod(0o755)
        if not args.no_path:
            import shlex
            profile=Path.home()/'.profile'
            before=profile.read_text() if profile.exists() else ''
            block='\n# >>> codex-token-saver >>>\nexport PATH='+shlex.quote(str(bindir))+':"$PATH"\n# <<< codex-token-saver <<<\n'
            if '# >>> codex-token-saver >>>' not in before:
                profile.write_text(before+block)
                info['profile_block']=block
    receipt.write_text(json.dumps(info,indent=2),encoding='utf-8')
    print('\nCodex Token Saver v0.1.0-beta.1\n[OK] Codex detected\n[OK] RTK ready\n[OK] CCE ready\n[OK] Savings telemetry ready\n[OK] Configuration installed\nStatus: ON\n\nRun Codex normally: codex\nOpen dashboard: codex-saver ui\nReview the installed hooks at first Codex launch. Open a new terminal if PATH has not refreshed.')


if __name__=='__main__': main()
