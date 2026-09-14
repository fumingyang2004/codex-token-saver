"""Build platform RTK observer from the fixed independent upstream revision."""
from pathlib import Path
import subprocess
import sys
from codex_token_saver.dependencies import original_rtk

root=Path(__file__).resolve().parents[1]
source=root/'.work/rtk'; source.parent.mkdir(exist_ok=True)
subprocess.run(['git','-c','core.autocrlf=false','clone','--depth','1','--branch','v0.48.0','https://github.com/rtk-ai/rtk.git',str(source)],check=True)
binary=original_rtk(root/'.work/original')
subprocess.run([sys.executable,str(root/'scripts/build_rtk_observer.py'),'--source',str(source),'--base-binary',binary],check=True)
