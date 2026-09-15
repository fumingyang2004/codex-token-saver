"""Keep pinned CCE indexing off the MCP event loop, without changing retrieval."""
import asyncio
import hashlib
import os
from pathlib import Path
import threading
import time

from .state import write_json, safe_path
from .savings import best_effort


def install(directory):
    from context_engine.integration import mcp_server
    from context_engine.indexer import pipeline
    from mcp.types import TextContent
    from .cce_observation import PIN
    if hashlib.sha256(Path(mcp_server.__file__).read_bytes()).hexdigest() != PIN:
        raise RuntimeError("Unsupported CCE source; runtime adapter not applied")
    if getattr(pipeline, "_saver_worker", False):
        return
    pipeline._saver_worker = True
    from .cce_index_policy import install as install_policy
    policy = install_policy(directory)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="cce-index-worker").start()
    state = {"phase": "idle", "pid": os.getpid(), "policy": policy, "queued": 0}
    directory = Path(directory)
    safe_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    last_write = 0.0
    def report(phase, force=True, **values):
        nonlocal last_write
        state.update(phase=phase, updated=time.time(), **values)
        if force or time.monotonic()-last_write >= .5:
            best_effort(write_json, directory / (str(os.getpid()) + ".json"), state)
            last_write = time.monotonic()

    original = pipeline.run_indexing
    def acquire():
        safe_path(directory / "index.lock")
        handle = (directory / "index.lock").open("a+b")
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            handle.close()
            return None

    serial = asyncio.Lock()
    async def locked_work(args, kwargs):
        # A file lock also serializes independent MCP processes for this project.
        handle = None
        try:
            report("waiting_for_index_lock")
            while handle is None:
                handle = acquire()
                if handle is None:
                    await asyncio.sleep(.25)
            report("indexing", started=time.time(), files_done=0, files_total=None,
                   chunks_done=0, chunks_total=None, stage="scan")
            kwargs = dict(kwargs)
            for name, fields in [('progress_fn', ('files_done', 'files_total')),
                                 ('embed_progress_fn', ('chunks_done', 'chunks_total'))]:
                previous = kwargs.get(name)
                def progress(current, total, fields=fields, previous=previous):
                    report('indexing', force=False, **dict(zip(fields, (current,total))))
                    if previous:
                        previous(current, total)
                kwargs[name] = progress
            phase = kwargs.get('phase_fn')
            def stage(value):
                report('indexing', stage=value)
                if phase:
                    phase(value)
            kwargs['phase_fn'] = stage
            result = await original(*args, **kwargs)
            report("ready" if not result.errors else "degraded", indexed_this_run=result.total_chunks,
                   errors=len(result.errors))
            return result
        except Exception as exc:
            report("failed", error=type(exc).__name__)
            raise
        finally:
            if handle:
                handle.close()

    async def work(args, kwargs):
        state['queued'] += 1
        waiting = True
        try:
            async with serial:
                state['queued'] -= 1
                waiting = False
                return await locked_work(args, kwargs)
        finally:
            if waiting:
                state['queued'] -= 1

    async def indexing(*args, **kwargs):
        # Do not cancel an in-progress index when an MCP request is cancelled.
        future = asyncio.run_coroutine_threadsafe(work(args, kwargs), loop)
        return await asyncio.shield(asyncio.wrap_future(future))
    pipeline.run_indexing = indexing

    cls = mcp_server.ContextEngineMCP
    search = cls._handle_context_search
    status = cls._handle_index_status
    async def guarded_search(self, args):
        if state['phase'] == 'idle' and (best_effort(self._backend._vector_store.count) or 0):
            # Serve cached content immediately, while checking edits made while
            # the previous server was offline. Do not rebuild a warm index.
            report('refresh_scheduled')
            async def refresh():
                try:
                    await indexing(self._config, self._project_dir, full=False)
                except Exception:
                    pass  # locked_work already records the failure
            asyncio.create_task(refresh())
        if state["phase"] in ("indexing", "waiting_for_index_lock"):
            count = best_effort(self._backend._vector_store.count) or 0
            if not count:
                return [TextContent(type="text", text="CCE index is updating in the background; use native file tools for this task and retry later.")]
        if state["phase"] in ("failed", "degraded"):
            return [TextContent(type="text", text="CCE indexing failed or completed with errors; retrieval readiness is not confirmed. Use native file tools and inspect CCE runtime diagnostics.")]
        return await search(self, args)
    async def index_status(self):
        # Explicit phase is useful even while another thread loads the model.
        if state["phase"] in ("indexing", "waiting_for_index_lock", "failed", "degraded"):
            count = best_effort(self._backend._vector_store.count) or 0
            return [TextContent(type="text", text="CCE index state: " + state["phase"] +
                f"; indexed chunks: {count}" +
                f"; files {state.get('files_done', 0)}/{state.get('files_total', '?')}, " +
                f"embedding chunks {state.get('chunks_done', 0)}/{state.get('chunks_total', '?')}; " +
                f"queued {state['queued']}. Existing indexed content remains searchable.")]
        result = await status(self)
        count = self._backend._vector_store.count()
        prefix = f"CCE indexed chunks: {count}. " + ("Index is empty; first search starts background indexing.\n" if count == 0 else "\n")
        result[0].text = prefix + result[0].text
        return result
    cls._handle_context_search = guarded_search
    cls._handle_index_status = index_status
    report("idle")
