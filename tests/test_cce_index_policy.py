import asyncio
import json
import threading
import time
from types import SimpleNamespace

from codex_token_saver import cce_index_policy, cce_runtime
from codex_token_saver.state import write_json


def test_source_policy_preserves_code_and_user_exclusions(tmp_path, monkeypatch):
    from context_engine import config
    from context_engine.indexer import ignorefile, watcher, pipeline
    import fastembed
    calls = []
    class Model:
        def __init__(self,*args,**kwargs):calls.append(kwargs)
    monkeypatch.setattr(fastembed,'TextEmbedding',Model)
    for module,name in [(config,'load_config'),(ignorefile,'load_ignore_patterns'),(watcher._DebouncedHandler,'_should_ignore'),
                        (pipeline,'_run_indexing_locked'),(pipeline,'_iter_project_files')]:
        monkeypatch.setattr(module,name,getattr(module,name))
    runtime = tmp_path/'state/projects/key/cce-runtime'
    policy = cce_index_policy.install(runtime)
    project = tmp_path/'project';project.mkdir()
    files=['src/code.py','results/run/raw/1.json','results/run/report_data/1.js','report_ui/style.css','.work/vendor.py','private.py']
    for name in files:
        p=project/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('content')
    (project/'.cceignore').write_text('private.py\n')
    cfg=config.load_config(global_path=tmp_path/'absent',project_path=tmp_path/'absent2')
    selected={p.relative_to(project).as_posix() for p in pipeline._iter_project_files(project,set(cfg.indexer_ignore),set(),cceignore_patterns=ignorefile.load_ignore_patterns(project))}
    assert 'src/code.py' in selected and 'report_ui/style.css' in selected
    assert not selected.intersection(files[1:3]+files[4:])
    fastembed.TextEmbedding('test',cache_dir='local')
    assert calls[-1]['threads']==2
    assert str(runtime.parent) in cfg.storage_path and policy['storage']==cfg.storage_path


def test_source_policy_can_explicitly_include_generated_data(tmp_path,monkeypatch):
    from context_engine import config
    from context_engine.indexer import ignorefile,watcher,pipeline
    import fastembed
    for module,name in [(config,'load_config'),(ignorefile,'load_ignore_patterns'),(watcher._DebouncedHandler,'_should_ignore'),(fastembed,'TextEmbedding'),
                        (pipeline,'_run_indexing_locked'),(pipeline,'_iter_project_files')]:
        monkeypatch.setattr(module,name,getattr(module,name))
    write_json(tmp_path/'state/settings.json',{'cce_index_policy':{'exclude_patterns':[],'exclude_directories':[],'threads':3}})
    value=cce_index_policy.install(tmp_path/'state/projects/key/cce-runtime')
    assert value['exclude_patterns']==[] and value['threads']==3


def test_queued_index_does_not_hide_active_index_and_partial_search(tmp_path,monkeypatch):
    from context_engine.indexer import pipeline
    from context_engine.integration import mcp_server
    from mcp.types import TextContent
    entered=threading.Event();release=threading.Event();runs=[]
    async def indexing(*args,**kwargs):
        runs.append(1);entered.set()
        while not release.is_set():await asyncio.sleep(.01)
        return SimpleNamespace(errors=[],total_chunks=3)
    async def search(self,args):return [TextContent(type='text',text='existing indexed source')]
    cls=mcp_server.ContextEngineMCP
    monkeypatch.setattr(cce_index_policy,'install',lambda directory:{})
    monkeypatch.setattr(pipeline,'_saver_worker',False,raising=False)
    monkeypatch.setattr(pipeline,'run_indexing',indexing)
    monkeypatch.setattr(cls,'_handle_context_search',search)
    monkeypatch.setattr(cls,'_handle_index_status',cls._handle_index_status)
    cce_runtime.install(tmp_path/'runtime')
    async def check():
        a=asyncio.create_task(pipeline.run_indexing())
        while not entered.is_set():await asyncio.sleep(.01)
        b=asyncio.create_task(pipeline.run_indexing())
        await asyncio.sleep(.05)
        instance=cls.__new__(cls)
        instance._backend=SimpleNamespace(_vector_store=SimpleNamespace(count=lambda:3))
        text=(await instance._handle_index_status())[0].text
        assert 'state: indexing' in text and 'queued 1' in text
        assert (await instance._handle_context_search({}))[0].text=='existing indexed source'
        assert len(runs)==1
        release.set();await asyncio.gather(a,b)
        assert len(runs)==2
    try:asyncio.run(check())
    finally:release.set()
