import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
import psutil
from codex_token_saver import control, hooks, sessions, savings, webserver
from codex_token_saver.state import Store, write_json


@pytest.fixture
def store(tmp_path):
    project=tmp_path/'project space 中文'; project.mkdir(); (project/'.git').mkdir()
    home=tmp_path/'codex'; home.mkdir()
    return Store(tmp_path/'saver',project,home)


def native(store,sid='session-A'):
    path=store.codex_home/'sessions'/f'rollout-{sid}.jsonl';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'type':'session_meta','payload':{'id':sid,'cwd':str(store.project),'source':'cli'}})+'\n')
    return path


def test_configuration_backup_disable_and_user_edits(store):
    config=store.codex_home/'config.toml'; initial=b'# user\nmodel="keep-me"\n'
    config.write_bytes(initial)
    other={'hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo user'}]}]}}
    (store.codex_home/'hooks.json').write_text(json.dumps(other))
    hook_before=(store.codex_home/'hooks.json').read_bytes()
    result=control.enable(store)
    assert Path(result['backup'],'config.toml').read_bytes()==initial
    control.disable(store)
    assert config.read_bytes()==initial and (store.codex_home/'hooks.json').read_bytes()==hook_before
    control.enable(store)
    config.write_bytes(config.read_bytes()+b'\n[unrelated]\npreserve=true\n')
    assert not control.disable(store)['conflicts']
    assert b'preserve=true' in config.read_bytes() and b'codex_token_saver_cce' not in config.read_bytes()


def test_conflicting_owned_mcp_is_preserved(store):
    control.enable(store)
    config=store.codex_home/'config.toml'
    config.write_text(config.read_text().replace('required = false','required = true'))
    result=control.disable(store)
    assert result['conflicts'] and 'required = true' in config.read_text()
    assert not store.enabled()


def test_new_user_hooks_keep_required_feature_enabled(store):
    control.enable(store)
    path=store.codex_home/'hooks.json';value=json.loads(path.read_text())
    own={'hooks':[{'type':'command','command':'echo new user hook'}]}
    value['hooks']['Stop'].append(own);path.write_text(json.dumps(value))
    assert not control.disable(store)['conflicts']
    assert own in json.loads(path.read_text())['hooks']['Stop']
    assert 'hooks = true' in (store.codex_home/'config.toml').read_text()


def test_ledger_aggregation_negative_duplicate_and_isolation(store):
    control.enable(store)
    a=savings.SavingsLedger(store,'A'); b=savings.SavingsLedger(store,'B')
    a.record_observed('rtk','one','a'*400,'b'*80,'real boundary fixture')
    a.record_observed('rtk','one','a'*400,'b'*80,'duplicate')
    a.record_observed('cce','two','a'*100,'b'*20,'real boundary fixture')
    a.record_observed('cce','overhead','','x'*16,'overhead')
    assert a.summary()['observed_net_avoided_tokens']==96
    assert b.summary()['observed_net_avoided_tokens'] is None
    control.disable(store)
    assert not a.record_observed('rtk','off','a'*40,'','off')
    assert a.summary()['observed_net_avoided_tokens']==96


def test_cce_native_pair_requires_delivered_nonce_and_identical_body(store):
    from codex_token_saver import cce_observation as cce
    from mcp.types import TextContent
    control.enable(store)
    directory=store.directory/'cce-observations';directory.mkdir(parents=True)
    result=cce.wrap_result([TextContent(type='text',text='compressed code + metadata')], [('chunk','raw '*300,'compressed code')], directory)
    response={'id':8,'result':{'content':[result[0].model_dump(by_alias=True,exclude_none=True)]}}
    item={'id':'native-item','tool':'context_search','result':response['result'],'status':'completed'}
    assert cce.native_event(store,'A',item,0) is None
    cce.delivered(store,response)
    event=cce.native_event(store,'A',item,0)
    assert event['delta_tokens']>0 and event['before_tokens']-event['after_tokens']==event['delta_tokens']
    item['result']['content'][0]['text']+=' changed after delivery'
    assert cce.native_event(store,'A',item,0) is None


def test_windows_hook_command_survives_spaces_and_unicode(store):
    import base64
    if os.name!='nt':pytest.skip('Windows command runner')
    command=control.hook_command(store)
    assert command.startswith('powershell.exe -NoProfile -NonInteractive -EncodedCommand ')
    script=base64.b64decode(command.split()[-1]).decode('utf-16le')
    assert str(store.root) in script and '_hook' in script


def test_registry_active_previous_and_no_session(store,monkeypatch):
    assert sessions.snapshot(store)['session'] is None
    path=native(store)
    process=psutil.Process()
    monkeypatch.setattr(sessions,'codex_parent',lambda:{'pid':process.pid,'created':process.create_time()})
    control.enable(store)
    sessions.register(store,{'session_id':'session-A','transcript_path':str(path),'cwd':str(store.project),'hook_event_name':'SessionStart'})
    assert sessions.snapshot(store)['session']['active']
    sessions.register(store,{'session_id':'session-A','transcript_path':str(path),'cwd':str(store.project),'hook_event_name':'SessionEnd'})
    assert sessions.snapshot(store)['session'] is None
    assert sessions.snapshot(store,'session-A')['session']['id']=='session-A'


@pytest.mark.parametrize('cmd',['git diff; evil','pytest | head','echo hello','git push','git -c alias.x=evil x','pytest $(bad)','python -m pytest'])
def test_complex_or_unsupported_commands_pass_through(cmd):
    assert hooks.arguments(cmd) is None


def test_hook_rewrite_preserves_other_input_and_does_not_execute(store,monkeypatch):
    control.enable(store);path=native(store)
    monkeypatch.setattr(sessions,'codex_parent',lambda:None)
    value=hooks.handle(store,{'session_id':'session-A','transcript_path':str(path),'cwd':str(store.project),
        'hook_event_name':'PreToolUse','tool_input':{'cmd':'pytest -vv','yield_time_ms':1000}})
    data=value['hookSpecificOutput']['updatedInput']
    assert '_rtk' in data['cmd'] and data['yield_time_ms']==1000


def test_cli_version():
    p=subprocess.run([sys.executable,'-m','codex_token_saver','version'],capture_output=True,text=True)
    assert p.returncode==0 and p.stdout.strip()=='Codex Token Saver 0.1.0-beta.1'


def test_ui_api_single_instance_privacy_and_no_session(store):
    url=webserver.start(store,False)
    try:
        info=webserver.running(store)
        assert url==webserver.start(store,False)
        assert webserver.request(info,'/api/session/current')['session'] is None
        assert b'Codex Token Saver' in urlopen(url).read()
        with pytest.raises(HTTPError): urlopen(url.split('/?')[0]+'/api/status')
        req=Request(url,headers={'Host':'evil.invalid'})
        with pytest.raises(HTTPError): urlopen(req)
    finally: webserver.stop(store)
