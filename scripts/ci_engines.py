"""Build platform RTK observer from the fixed independent upstream revision."""
from pathlib import Path
import subprocess
import sys
from codex_token_saver.dependencies import original_rtk

root=Path(__file__).resolve().parents[1]
source=root/'.work/rtk'; source.parent.mkdir(exist_ok=True)
# Rust cache may have restored target/ before source checkout. Preserve it.
if source.exists() and any(p.name != 'target' for p in source.iterdir()):
    raise SystemExit('Unexpected files in isolated RTK build directory')
subprocess.run(['git','init',str(source)],check=True)
subprocess.run(['git','config','core.autocrlf','false'],cwd=source,check=True)
subprocess.run(['git','remote','add','origin','https://github.com/rtk-ai/rtk.git'],cwd=source,check=True)
subprocess.run(['git','fetch','--depth','1','origin','tag','v0.48.0'],cwd=source,check=True)
subprocess.run(['git','checkout','--detach','FETCH_HEAD'],cwd=source,check=True)
binary=original_rtk(root/'.work/original')
subprocess.run([sys.executable,str(root/'scripts/build_rtk_observer.py'),'--source',str(source),'--base-binary',binary],check=True)
