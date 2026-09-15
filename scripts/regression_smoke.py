"""Real engine/protocol smoke; isolated state, no model agent or global config edits."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from codex_token_saver import hooks, sessions
from codex_token_saver.runtime import capture_stderr, lines_to_queue, stop_process
from codex_token_saver.state import Store, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('area', type=Path)
    parser.add_argument('--installed-home', required=True, type=Path)
    args = parser.parse_args()
    area = args.area.resolve(); area.mkdir(parents=True, exist_ok=False)
    project = area/'project'; child = project/'child'; child.mkdir(parents=True)
    (child/'test_example.py').write_text('from pathlib import Path\ndef test_location():\n assert Path.cwd().name == "child"\n')
    (project/'inventory.py').write_text('def settle_reservation(inventory, reserved):\n    """Settle inventory reservations and return an audit ledger entry."""\n    available = inventory - reserved\n    return {"available": available, "settled": reserved}\n')
    store = Store(area/'state', project, area/'codex')
    deps = json.loads((args.installed_home/'dependencies.json').read_text())
    write_json(store.state_path, {'enabled':True})
    write_json(store.root/'dependencies.json', deps)
    # Reuse a copy of downloaded weights, with no shared index or model writes.
    shutil.copytree(args.installed_home/'engine-home/models', store.root/'engine-home/models')
    rollout = store.codex_home/'sessions/rollout-engine-smoke.jsonl'
    rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps({'type':'session_meta','payload':{'id':'engine-smoke','cwd':str(project)}})+'\n')
    env = {**os.environ, 'PYTHONPATH':str(Path(hooks.__file__).parents[1]), 'PYTHONUTF8':'1',
           'PATH':str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH']}
    payload = {'session_id':'engine-smoke','transcript_path':str(rollout),'cwd':str(project),
               'hook_event_name':'PreToolUse','tool_input':{'command':'pytest test_example.py -vv'}}
    command = hooks.handle(store, payload)['hookSpecificOutput']['updatedInput']['command']
    started = time.monotonic()
    rtk = subprocess.run(['powershell.exe','-NoProfile','-Command',command] if os.name=='nt' else ['sh','-c',command],
                         cwd=child, env=env, capture_output=True, text=True, encoding='utf-8', timeout=40)
    assert rtk.returncode == 0, rtk.stdout + rtk.stderr
    result = {'scope':'real RTK/CCE binaries, synthetic hook input; not native Codex acceptance',
              'rtk':{'code':rtk.returncode,'seconds':round(time.monotonic()-started,3), 'output':rtk.stdout},'cce':[]}
    snapshot = sessions.snapshot(store, 'engine-smoke')
    assert snapshot['summary']['components']['rtk']['invocations'] == 1
    result['rtk']['summary'] = snapshot['summary']['components']['rtk']
    proc = subprocess.Popen([sys.executable,'-m','codex_token_saver','--home',str(store.root),
                             '--codex-home',str(store.codex_home),'_mcp'], cwd=project, env=env,
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    capture_stderr(proc); messages = queue.Queue()
    threading.Thread(target=lines_to_queue,args=(proc.stdout,messages),daemon=True).start()
    ident = 0
    def request(method, params):
        nonlocal ident
        ident += 1; start = time.monotonic()
        proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':ident,'method':method,'params':params})+'\n');proc.stdin.flush()
        while True:
            response = messages.get(timeout=max(.01, 55-(time.monotonic()-start)))
            assert response is not None, list(proc.cce_stderr_tail)
            if response.get('id') == ident:
                assert 'error' not in response, response
                value=response['result'];elapsed=round(time.monotonic()-start,3)
                result['cce'].append({'method':params.get('name',method),'seconds':elapsed,'result':value})
                print(json.dumps({'method':params.get('name',method),'seconds':elapsed,
                                  'text':str(value.get('content',''))[:180]}),flush=True)
                return value
    try:
        request('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'regression','version':'1'}})
        proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n');proc.stdin.flush()
        request('tools/list', {})
        request('tools/call', {'name':'context_search','arguments':{'query':'settle_reservation inventory audit','top_k':2,'max_tokens':2000}})
        deadline = time.monotonic()+150
        while time.monotonic() < deadline:
            status=request('tools/call', {'name':'index_status','arguments':{}})
            states=[json.loads(p.read_text()) for p in (store.directory/'cce-runtime').glob('*.json')]
            if any(v['phase']=='ready' for v in states):
                break
            assert not any(v['phase']=='failed' for v in states), states
            time.sleep(2)
        else:
            raise RuntimeError('Index did not complete within bounded smoke')
        found=request('tools/call', {'name':'context_search','arguments':{'query':'settle_reservation inventory audit','top_k':2,'max_tokens':2000}})
        assert not found.get('isError') and 'settle_reservation' in json.dumps(found), found
        assert (found['content'][0].get('_meta') or {}).get('codex_token_saver/cce_observation'), found
        result['ok']=True
    finally:
        stop_process(proc)
        write_json(area/'result.json',result)
    print(json.dumps({'ok':True,'report':str(area/'result.json'),'rtk':result['rtk']['summary']}))


if __name__=='__main__':
    main()
