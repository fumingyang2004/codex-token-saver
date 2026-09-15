"""Real Git + bundled RTK + hook/ledger regressions; no native Codex host."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from codex_token_saver import hooks, sessions
from codex_token_saver.rtk_observation import outcome
from codex_token_saver.savings import SavingsLedger
from codex_token_saver.state import Store, write_json


def test_diff_exit_semantics(tmp_path):
    store = Store(tmp_path / 'state', tmp_path, tmp_path / 'codex')
    write_json(store.state_path, {'enabled': True})
    ledger = SavingsLedger(store, 'outcomes')
    for n, (args, code, failed) in enumerate([
        (['git', 'diff', '--no-index', 'a', 'b'], 1, False),
        (['git', 'diff', '--exit-code'], 1, False),
        (['git', 'diff', '--quiet'], 1, False),
        (['git', 'diff', '--check'], 1, True),
        (['git', 'diff', '--', '--check', 'b'], 1, False),
        (['git', 'diff'], 128, True),
        (['pytest'], 1, True),
    ]):
        metadata = outcome(args, code)
        assert metadata['failed'] == failed
        ledger.record_invocation('rtk', str(n), 'test', metadata=metadata)
    assert ledger.summary()['components']['rtk']['failed'] == 3
    assert outcome(['git', 'diff', '--no-index'], 1, b"error: Could not access missing")['failed']
    assert not outcome(['git', 'diff', '--no-index'], 1, b"warning: CRLF\n")['failed']


@pytest.mark.parametrize('case', ['stat', 'compact', 'identical', 'invalid', 'implicit', 'quiet', 'repo_diff', 'repo_check', 'fallback'])
def test_real_git_diff_output_and_measurement(tmp_path, case):
    assets = Path(hooks.__file__).parent / 'assets/rtk-observer'
    manifest_path = assets / 'manifest.json'
    if not manifest_path.exists():
        pytest.skip('platform RTK observer not built')
    manifest = json.loads(manifest_path.read_text())
    if manifest['platform'] != sys.platform:
        pytest.skip('observer for a different platform')
    # CI builds/downloads both binaries; local development may use its install.
    base = Path(__file__).resolve().parents[1] / '.work/original' / ('rtk.exe' if os.name == 'nt' else 'rtk')
    installed = Path(os.environ.get('LOCALAPPDATA', '')) / 'CodexTokenSaver'
    deps_path = installed / 'dependencies.json'
    deps = {'rtk': str(base)} if base.is_file() else (json.loads(deps_path.read_text()) if deps_path.exists() else {})
    if not deps.get('rtk') or not shutil.which('git'):
        pytest.skip('real engine dependencies unavailable')
    project = tmp_path / 'project'; project.mkdir()
    child = project / 'child'; child.mkdir()
    (child / 'a.txt').write_text(''.join(f'line {n} original content\n' for n in range(80)))
    (child / 'b.txt').write_text(''.join(f'line {n} changed content\n' for n in range(80)))
    env = {**os.environ, 'PYTHONPATH': str(Path(hooks.__file__).parents[1]), 'PYTHONUTF8': '1',
           'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.autocrlf', 'GIT_CONFIG_VALUE_0': 'false'}
    if case == 'fallback': env['CODEX_SAVER_RTK_OBSERVER'] = '0'
    if case.startswith('repo_'):
        subprocess.run(['git', 'init', '-q'], cwd=child, env=env, check=True)
        subprocess.run(['git', 'add', 'a.txt'], cwd=child, env=env, check=True)
        (child / 'a.txt').write_text('changed content  \n')
        args = ['git', 'diff', '--check' if case == 'repo_check' else '--exit-code']
    else:
        args = ['git', 'diff', '--no-index']
        if case in ('stat', 'identical', 'invalid', 'fallback'): args += ['--stat']
        if case == 'quiet': args += ['--quiet']
        if case == 'implicit': args = ['git', 'diff']
        args += ['--', 'a.txt', 'a.txt' if case == 'identical' else ('missing.txt' if case == 'invalid' else 'b.txt')]
    native = subprocess.run(args, cwd=child, env=env, capture_output=True, timeout=20)
    store = Store(tmp_path / 'state', project, tmp_path / 'codex')
    write_json(store.state_path, {'enabled': True})
    write_json(store.root / 'dependencies.json', deps)
    sid = 'git-diff-' + case
    rollout = store.codex_home / f'sessions/rollout-{sid}.jsonl'; rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(project)}}) + '\n')
    command = hooks.handle(store, {'hook_event_name': 'PreToolUse', 'session_id': sid,
        'transcript_path': str(rollout), 'cwd': str(project),
        'tool_input': {'command': ' '.join(args)}})['hookSpecificOutput']['updatedInput']['command']
    wrapped = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command] if os.name == 'nt' else ['sh', '-c', command],
                             cwd=child, env=env, capture_output=True, timeout=30)
    assert wrapped.returncode == native.returncode, (wrapped.stdout, wrapped.stderr)
    if case in ('stat', 'identical', 'invalid', 'quiet', 'repo_check', 'fallback'):
        assert wrapped.stdout == native.stdout
        assert wrapped.stderr == native.stderr
    else:
        assert b'changed content' in wrapped.stdout
    snapshot = sessions.snapshot(store, sid)
    comp = snapshot['summary']['components']['rtk']
    if case == 'fallback':
        assert comp['events'] == 0 and comp['saved'] is None
        return
    assert comp['invocations'] == comp['events'] == 1, comp
    assert comp['failed'] == (1 if case in ('invalid', 'repo_check') else 0), comp
    if case in ('stat', 'identical', 'invalid', 'quiet', 'repo_check'):
        assert comp['saved'] == 0, comp
