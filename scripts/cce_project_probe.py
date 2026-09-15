"""Bounded CCE probe on a real project, with isolated state and cached weights."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from codex_token_saver.state import Store, write_json
from codex_token_saver.runtime import capture_stderr, lines_to_queue, stop_process


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--area',required=True,type=Path)
    parser.add_argument('--installed-home',required=True,type=Path)
    parser.add_argument('--query',default='MATH500 OlymMATH MODEL workflow configuration')
    args=parser.parse_args()
    area=args.area.resolve();area.mkdir(parents=True,exist_ok=False)
    store=Store(area/'state',args.project,area/'codex')
    write_json(store.state_path,{'enabled':True})
    write_json(store.root/'dependencies.json',json.loads((args.installed_home/'dependencies.json').read_text()))
    shutil.copytree(args.installed_home/'engine-home/models',store.root/'engine-home/models')
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'app'),'PYTHONUTF8':'1'}
    results=[]
    for warm in (False,True):
        started=time.monotonic()
        first_result_seconds = None
        proc=subprocess.Popen([sys.executable,'-m','codex_token_saver','--home',str(store.root),
            '--codex-home',str(store.codex_home),'_mcp'],cwd=store.project,env=env,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        capture_stderr(proc); q=queue.Queue();calls=[];ident=0
        threading.Thread(target=lines_to_queue,args=(proc.stdout,q),daemon=True).start()
        def request(method,params):
            nonlocal ident
            ident+=1;t=time.monotonic()
            proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':ident,'method':method,'params':params})+'\n');proc.stdin.flush()
            while True:
                r=q.get(timeout=max(.01,55-(time.monotonic()-t)))
                assert r is not None,list(proc.cce_stderr_tail)
                if r.get('id')==ident:
                    assert 'error' not in r,r
                    value=r['result'];calls.append({'tool':params.get('name',method),'seconds':round(time.monotonic()-t,3),'result':value})
                    return value
        try:
            request('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'project-probe','version':'1'}})
            proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n');proc.stdin.flush()
            request('tools/list',{})
            first=request('tools/call',{'name':'context_search','arguments':{'query':args.query,'top_k':5,'max_tokens':3000}})
            if not warm:
                deadline=time.monotonic()+240
                while time.monotonic()<deadline:
                    state=request('tools/call',{'name':'index_status','arguments':{}})
                    print(json.dumps({'elapsed':round(time.monotonic()-started,2),'state':state.get('content')},ensure_ascii=True),flush=True)
                    match=re.search(r'indexed chunks: (\d+)',str(state),re.I)
                    if match and int(match[1])>0 and first_result_seconds is None:
                        partial=request('tools/call',{'name':'context_search','arguments':{'query':args.query,'top_k':5,'max_tokens':3000}})
                        if not partial.get('isError') and 'codex_token_saver/cce_observation' in str(partial):
                            first_result_seconds=round(time.monotonic()-started,3)
                    records=[json.loads(p.read_text(encoding='utf-8')) for p in (store.directory/'cce-runtime').glob('*.json')]
                    if any(r['phase']=='ready' for r in records):break
                    assert not any(r['phase']=='failed' for r in records),records
                    time.sleep(2)
                else:raise RuntimeError('Index exceeded 240-second validation budget')
                first=request('tools/call',{'name':'context_search','arguments':{'query':args.query,'top_k':5,'max_tokens':3000}})
            assert not first.get('isError') and any('codex_token_saver/cce_observation' in str(c.get('_meta',{})) for c in first.get('content',[])),first
            results.append({'warm':warm,'total_seconds':round(time.monotonic()-started,3),
                            'first_result_seconds':first_result_seconds or round(time.monotonic()-started,3),'calls':calls})
        finally:
            stop_process(proc)
            write_json(area/'result.json',{'runs':results,'last_calls':calls})
    print(json.dumps({'ok':True,'runs':[{'warm':r['warm'],'seconds':r['total_seconds']} for r in results],'report':str(area/'result.json')}))


if __name__=='__main__':main()
