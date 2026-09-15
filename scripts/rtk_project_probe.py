"""Read-only real-directory RTK verification, with isolated synthetic sessions."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from codex_token_saver import hooks, sessions
from codex_token_saver.savings import SavingsLedger
from codex_token_saver.state import Store, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--left', required=True)
    parser.add_argument('--right', required=True)
    parser.add_argument('--area', type=Path, required=True)
    parser.add_argument('--installed-home', type=Path, required=True)
    args = parser.parse_args()
    args.area.mkdir(parents=True, exist_ok=False)
    result = {'scope': 'real Git and RTK, synthetic hook/session, isolated ledger; no native Codex acceptance', 'cases': []}
    for case, flags in [('stat', ['--stat']), ('compact', [])]:
        sid = 'rtk-project-' + case
        store = Store(args.area / case / 'state', args.project, args.area / case / 'codex')
        write_json(store.state_path, {'enabled': True})
        write_json(store.root / 'dependencies.json', json.loads((args.installed_home / 'dependencies.json').read_text()))
        rollout = store.codex_home / f'sessions/rollout-{sid}.jsonl'
        rollout.parent.mkdir(parents=True)
        rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(args.project)}}) + '\n')
        command_args = ['git', 'diff', '--no-index', *flags, '--', args.left, args.right]
        # Paths are structured for native Git; shell command construction uses
        # the product's existing quoting for hook input.
        original = 'git diff --no-index ' + ('--stat ' if flags else '') + '-- ' + ' '.join("'" + p.replace("'", "''") + "'" for p in [args.left, args.right])
        command = hooks.handle(store, {'hook_event_name': 'PreToolUse', 'session_id': sid,
            'transcript_path': str(rollout), 'cwd': str(args.project),
            'tool_input': {'command': original}})['hookSpecificOutput']['updatedInput']['command']
        native = subprocess.run(command_args, cwd=args.project, capture_output=True, timeout=30)
        start = time.monotonic()
        wrapped = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command] if os.name == 'nt' else ['sh', '-c', command],
                                 cwd=args.project, capture_output=True, timeout=30)
        seconds = round(time.monotonic() - start, 3)
        assert wrapped.returncode == native.returncode
        if case == 'stat':
            assert wrapped.stdout == native.stdout and wrapped.stderr == native.stderr
        snapshot = sessions.snapshot(store, sid)
        comp = snapshot['summary']['components']['rtk']
        assert comp['events'] == comp['invocations'] == 1 and comp['failed'] == 0, comp
        observed = [e for e in SavingsLedger(store, sid).events() if e['kind'] == 'observed'][0]
        result['cases'].append({'case': case, 'seconds': seconds, 'exit_code': wrapped.returncode,
            'summary': comp, 'before_tokens': observed['before_tokens'], 'after_tokens': observed['after_tokens'],
            'stdout': wrapped.stdout.decode('utf-8', errors='replace'), 'stderr': wrapped.stderr.decode('utf-8', errors='replace')})
    write_json(args.area / 'result.json', result)
    print(json.dumps({c['case']: {k:c[k] for k in ('seconds','exit_code','before_tokens','after_tokens','summary')} for c in result['cases']}))


if __name__ == '__main__':
    main()
