"""Relocated package lifecycle; fake Codex detection only, no model claims."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

root=Path(__file__).resolve().parents[1]
area=Path(tempfile.mkdtemp(prefix='saver relocated 中文 '))
bundle=area/'bundle'; install=area/'install'; codex=area/'codex'; codex.mkdir()
config=codex/'config.toml'; original=b'# existing user configuration\n';config.write_bytes(original)
archive=next((root/'dist').glob('*.zip' if os.name=='nt' else '*.tar.gz'))
shutil.unpack_archive(archive,bundle)
fake=area/'fake';fake.mkdir()
stub=fake/('codex.cmd' if os.name=='nt' else 'codex')
stub.write_text('@echo off\necho codex-cli 0.154.0\n' if os.name=='nt' else '#!/bin/sh\necho codex-cli 0.154.0\n');stub.chmod(0o755)
env={**os.environ,'PATH':str(fake)+os.pathsep+os.environ['PATH'],'PYTHONUTF8':'1'}
env.pop('CODEX_THREAD_ID',None);env.pop('CODEX_SESSION_ID',None)
if os.name=='nt':
    command=['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(bundle/'install.ps1'),'-InstallHome',str(install),'-CodexHome',str(codex),'-NoPath']
else:
    env['CODEX_SAVER_HOME']=str(install)
    command=['sh',str(bundle/'install.sh'),'--codex-home',str(codex),'--no-path']
subprocess.run(command,check=True,env=env,cwd=area)
python=install/('venv/Scripts/python.exe' if os.name=='nt' else 'venv/bin/python')
cli=[str(python),'-m','codex_token_saver','--home',str(install)]
def run(*args):return subprocess.check_output([*cli,*args],env=env,cwd=area,text=True,encoding='utf-8')
assert json.loads(run('doctor','--json'))['ready']
observer=subprocess.check_output([str(python),'-c','from codex_token_saver.dependencies import ASSETS; import json; m=json.loads((ASSETS/"rtk-observer/manifest.json").read_text()); print(ASSETS/"rtk-observer"/m["binary"])'],env=env,text=True,encoding='utf-8').strip()
assert '0.48.0' in subprocess.check_output([observer,'--version'],text=True)
assert json.loads(run('status','--json'))['status']=='ON'
url=run('ui','--no-browser').strip().splitlines()[-1]
from urllib.parse import urlsplit,parse_qs
parsed=urlsplit(url);token=parse_qs(parsed.query)['token'][0]
req=urllib.request.Request(f'http://127.0.0.1:{parsed.port}/api/session/current',headers={'X-Saver-Token':token})
assert json.load(urllib.request.urlopen(req))['session'] is None
assert json.loads(run('savings','--json'))['session'] is None
run('disable');assert config.read_bytes()==original
run('enable');assert json.loads(run('status','--json'))['status']=='ON'
run('uninstall');assert config.read_bytes()==original
for _ in range(100):
    if not (install/'venv').exists():break
    time.sleep(.2)
assert not (install/'venv').exists(), 'Owned runtime cleanup incomplete'
assert codex.exists() and (codex/'backups').is_dir()
print('PASS: relocated install/doctor/UI/savings/disable/enable/uninstall; Codex version detection is a fixture, no native model session in CI.')
