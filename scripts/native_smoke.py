"""One authorized real CLI acceptance task against a relocated installed package.

Fixture/index preparation is separate. Raw evidence stays outside the repository.
Only the sanitized report should be committed. Auth is never printed or packaged.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    p=argparse.ArgumentParser();p.add_argument('area',type=Path);args=p.parse_args()
    area=args.area.resolve();installed=area/'installed';codexhome=area/'codex';project=area/'project'
    python=installed/('venv/Scripts/python.exe' if os.name=='nt' else 'venv/bin/python')
    cli=[str(python),'-m','codex_token_saver','--home',str(installed)]
    deps=json.loads((installed/'dependencies.json').read_text())
    env={**os.environ,'CODEX_HOME':str(codexhome),'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8',
         'PATH':str(python.parent)+os.pathsep+os.environ['PATH']}
    for key in ('CODEX_THREAD_ID','CODEX_SESSION_ID'):env.pop(key,None)
    if (area/'native-events.jsonl').exists():raise SystemExit('Existing evidence: use a new area for a new run')
    prompt=('This is a bounded product acceptance task. Do not delegate or edit files. '
        'Run exactly the plain shell command pytest test_long_output.py -vv once, without adding wrappers. '
        'Then use the native codex_token_saver_cce context_search tool exactly once with '
        'query="settle_reservation inventory reservation settlement audit ledger", top_k=2, max_tokens=2000. '
        'Do not retry either operation. Then run Start-Sleep -Seconds 25 once to allow the live dashboard acceptance capture. '
        'Finish with a brief truthful summary and TOKEN_SAVER_SMOKE_COMPLETE. Do not read additional files or run other commands.')
    # Explicitly vetted isolated generated hooks only; production installer never
    # bypasses hook trust or changes the user's native approval policy.
    command=[deps['codex'],'exec','-','--json','--color','never','--dangerously-bypass-hook-trust',
             '--sandbox','danger-full-access','-C',str(project)]
    auth=codexhome/'auth.json'
    try:
        shutil.copyfile(Path.home()/'.codex/auth.json',auth)
        with (area/'native-events.jsonl').open('wb') as out,(area/'native-stderr.txt').open('wb') as err:
            proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=out,stderr=err,cwd=project,env=env)
            proc.stdin.write(prompt.encode());proc.stdin.close()
            deadline=time.monotonic()+360
            live=[]
            while proc.poll() is None and time.monotonic()<deadline:
                try:
                    summary=json.loads(subprocess.check_output([*cli,'savings','--json'],env=env,cwd=project))
                    if summary['session'] and summary['session']['active']:
                        live.append({'timestamp':time.time(),'session':summary['session'],'summary':summary['summary']})
                        (area/'live-snapshot.json').write_text(json.dumps(live[-1],indent=2))
                except (subprocess.CalledProcessError,ValueError):pass
                time.sleep(1)
            if proc.poll() is None:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
                else:proc.kill()
                raise SystemExit('Bounded native smoke timed out')
        events=[json.loads(row) for row in (area/'native-events.jsonl').read_text().splitlines() if row.startswith('{')]
        sid=next(e['thread_id'] for e in events if e.get('type')=='thread.started')
        result=json.loads(subprocess.check_output([*cli,'savings','--session-id',sid,'--json'],env=env,cwd=project))
        (area/'native-summary.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({'code':proc.returncode,'session_id':sid,'active_samples':len(live),'summary':result['summary']},indent=2))
    finally:
        auth.unlink(missing_ok=True)


if __name__=='__main__':main()
