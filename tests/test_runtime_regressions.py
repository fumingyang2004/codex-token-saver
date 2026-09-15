"""Regressions from native session 01a0a063, including non-Git subdirectories."""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from codex_token_saver import cli, hooks, sessions, savings, rtk_spool
from codex_token_saver.state import Store, write_json


def fixture(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    child = project / 'child'; child.mkdir()
    store = Store(tmp_path / 'state', project, tmp_path / 'codex')
    write_json(store.state_path, {'enabled': True})
    path = store.codex_home / 'sessions/rollout-session-A.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'session-A', 'cwd': str(project)}})+'\n')
    return store, child, path


def test_rtk_real_process_preserves_subdirectory_and_exit_status(tmp_path, monkeypatch):
    store, child, path = fixture(tmp_path)
    monkeypatch.setattr(rtk_spool.tempfile, 'tempdir', str(tmp_path))
    # Simulate host normalization: it omits workdir from hook input, but starts
    # the emitted command in the correct child directory.
    payload = {'session_id': 'session-A', 'transcript_path': str(path), 'cwd': str(store.project),
               'hook_event_name': 'PreToolUse', 'tool_input': {'command': 'pytest test_cwd.py -q'}}
    command = hooks.handle(store, payload)['hookSpecificOutput']['updatedInput']['command']
    (child / 'test_cwd.py').write_text('from pathlib import Path\ndef test_cwd():\n assert Path.cwd().name == "child"\n assert False, "INTENTIONAL_FAILURE"\n')
    # Missing RTK uses the original pytest, exercising the actual CLI wrapper.
    env = {**os.environ, 'PYTHONPATH': str(Path(hooks.__file__).parents[1]),
           'PATH': str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH']}
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command] if os.name == 'nt'
                          else ['sh', '-c', command], cwd=child, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 1 and 'INTENTIONAL_FAILURE' in proc.stdout
    records = list((store.directory/'rtk-pending').glob('*.json'))
    assert len(records) == 1
    pending = json.loads(records[0].read_text())
    started = json.loads((Path(pending['directory'])/'started.json').read_text())
    assert Path(started['cwd']) == child and pending['project'] == str(store.project)
    assert not Store(store.root, child, store.codex_home).directory.exists()


def test_native_cce_failures_count_without_crediting_late_observation(tmp_path):
    store, child, path = fixture(tmp_path)
    results = [('completed', {'content': [{'type':'text','text':'Index empty'}]}, None),
               ('failed', None, {'message':'timed out awaiting tools/call after 60s'}),
               ('completed', {'isError':True,'content':[{'type':'text','text':'CCE request timed out after 45s'}]}, None)]
    with path.open('a') as f:
        for n, (status, result, error) in enumerate(results):
            f.write(json.dumps({'type':'event_msg','payload':{'type':'item_completed','thread_id':'session-A',
                'item':{'type':'McpToolCall','id':str(n),'server':'codex_token_saver_cce','tool':'context_search',
                        'status':status,'result':result,'error':error}}})+'\n')
    write_json(store.root/'sessions/session-A.json', {'id':'session-A', 'project':str(store.project),
        'codex_home':str(store.codex_home),'rollout':str(path),'last_activity':0,'ended':True})
    for _ in range(2):
        comp = sessions.snapshot(store, 'session-A')['summary']['components']['cce']
        assert comp['invocations'] == 3 and comp['failed'] == comp['timed_out'] == 2
        assert comp['saved'] is None and comp['events'] == 0
    assert savings.SavingsLedger(store, 'session-B').events() == []


def test_ended_session_reclaims_only_empty_unconfirmed_spool(tmp_path, monkeypatch):
    store, _, _ = fixture(tmp_path)
    monkeypatch.setattr(rtk_spool.tempfile, 'tempdir', str(tmp_path))
    nonce = rtk_spool.allocate(store, 'session-A')
    assert rtk_spool.pending_status(store, 'session-A') == 1
    assert rtk_spool.pending_status(store, 'session-B', True) == 0
    assert rtk_spool.pending_status(store, 'session-A', True) == 0
    assert not (store.directory/'rtk-pending'/f'{nonce}.json').exists()


def test_cce_blocking_index_keeps_mcp_status_responsive(tmp_path, monkeypatch):
    from context_engine.indexer import pipeline
    from context_engine.integration import mcp_server
    from codex_token_saver import cce_runtime
    from codex_token_saver import cce_index_policy
    monkeypatch.setattr(cce_index_policy, 'install', lambda directory: {})
    entered = threading.Event()
    async def slow_index(*args, **kwargs):
        entered.set()
        time.sleep(.6)  # models upstream's synchronous model load / embedding
        return SimpleNamespace(errors=[], total_chunks=2)
    monkeypatch.setattr(pipeline, 'run_indexing', slow_index)
    monkeypatch.setattr(pipeline, '_saver_worker', False, raising=False)
    cls = mcp_server.ContextEngineMCP
    monkeypatch.setattr(cls, '_handle_context_search', cls._handle_context_search)
    monkeypatch.setattr(cls, '_handle_index_status', cls._handle_index_status)
    cce_runtime.install(tmp_path/'cce-runtime')
    async def check():
        task = asyncio.create_task(pipeline.run_indexing(None, tmp_path))
        while not entered.is_set():
            await asyncio.sleep(.01)
        instance = cls.__new__(cls)
        instance._backend = SimpleNamespace(_vector_store=SimpleNamespace(count=lambda:0))
        start = time.monotonic()
        result = await instance._handle_index_status()
        assert time.monotonic()-start < .2 and 'indexing' in result[0].text
        assert not task.done()
        await task
    asyncio.run(check())


def test_doctor_does_not_describe_installation_as_runtime_readiness(tmp_path):
    store, _, _ = fixture(tmp_path)
    write_json(store.directory/'cce-health.json', {'ok':False,'detail':'CCE output closed'})
    value = cli.status(store)
    assert value['cce_last_project_health']['ok'] is False
    assert value['cce'] != 'ready'


def test_opened_subfolder_does_not_widen_to_ancestor_git_root(tmp_path):
    (tmp_path/'.git').mkdir()
    opened = tmp_path/'opened'; opened.mkdir()
    store = Store(tmp_path/'state', opened, tmp_path/'codex')
    assert store.project == opened


def test_proxy_times_out_and_discards_cancelled_and_late_responses(tmp_path):
    import queue
    from codex_token_saver.runtime import lines_to_queue, stop_process
    store, child, _ = fixture(tmp_path)
    fake = tmp_path/'fake_mcp.py'
    fake.write_text('''import sys,json,threading,time
def reply(m):
 time.sleep(.8)
 print(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":{"content":[{"type":"text","text":"late"}]}}),flush=True)
for line in sys.stdin:
 m=json.loads(line)
 if "id" in m: threading.Thread(target=reply,args=(m,)).start()
''')
    code = '''import sys,subprocess
from codex_token_saver import runtime
from codex_token_saver.state import Store
runtime.CCE_REQUEST_TIMEOUT=.1
runtime.start_cce=lambda store: subprocess.Popen([sys.executable,'-u',sys.argv[1]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
runtime.mcp_proxy(Store(sys.argv[2],sys.argv[3],sys.argv[4]))
'''
    env = {**os.environ,'PYTHONPATH':str(Path(hooks.__file__).parents[1])}
    proc = subprocess.Popen([sys.executable,'-u','-c',code,str(fake),str(store.root),str(store.project),str(store.codex_home)],
        cwd=store.project,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    messages = queue.Queue()
    threading.Thread(target=lines_to_queue,args=(proc.stdout,messages),daemon=True).start()
    try:
        for value in [{'id':1,'method':'tools/call','params':{'name':'context_search'}},
                      {'method':'notifications/cancelled','params':{'requestId':1}},
                      {'id':2,'method':'tools/call','params':{'name':'context_search'}}]:
            proc.stdin.write(json.dumps(value)+'\n');proc.stdin.flush()
        response = messages.get(timeout=5)
        assert response['id'] == 2 and response['result']['isError']
        assert 'timed out' in response['result']['content'][0]['text']
        time.sleep(1)
        assert messages.empty(), 'cancelled or late responses must not reach the host'
    finally:
        proc.stdin.close()
        try: proc.wait(timeout=5)
        finally: stop_process(proc)
