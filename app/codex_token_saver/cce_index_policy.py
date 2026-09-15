"""Source-first local index policy; generated artifacts stay available natively."""
import hashlib
import json
import os
import inspect
import textwrap
from pathlib import Path

from .state import read_json, safe_path

DEFAULT_EXCLUDES = ['**/report_data/**', '**/results/**/raw/**']
PIPELINE_PIN = '01ace2f2b119340f1c3f9a2ff06268f068500e24b23fe05e33579373b0e4d18c'


def install(directory):
    from context_engine import config
    from context_engine.indexer import ignorefile, watcher, pipeline
    import fastembed
    directory = Path(directory)
    # cce-runtime lives under <home>/projects/<project-id>/.
    settings = read_json(directory.parents[2] / 'settings.json')
    policy = settings.get('cce_index_policy', {})
    excluded = policy.get('exclude_patterns', DEFAULT_EXCLUDES)
    ignored_dirs = policy.get('exclude_directories', ['.work', '.test-runtime'])
    if not isinstance(excluded, list) or not all(isinstance(p, str) for p in excluded):
        raise ValueError('cce_index_policy.exclude_patterns must be a string list')
    if not isinstance(ignored_dirs, list) or not all(isinstance(p, str) for p in ignored_dirs):
        raise ValueError('cce_index_policy.exclude_directories must be a string list')
    threads = policy.get('threads', 2)
    if type(threads) is not int or not 1 <= threads <= 8:
        raise ValueError('cce_index_policy.threads must be between 1 and 8')
    identity = hashlib.sha256(json.dumps([excluded, ignored_dirs], sort_keys=True).encode()).hexdigest()[:12]
    storage = directory.parent / ('cce-index-source-v1-' + identity)
    safe_path(storage)
    load = config.load_config
    def configured(*args, **kwargs):
        value = load(*args, **kwargs)
        value.storage_path = str(storage)
        value.indexer_ignore = list(dict.fromkeys([*value.indexer_ignore, *ignored_dirs]))
        return value
    config.load_config = configured
    patterns = ignorefile.load_ignore_patterns
    def merged(project):
        # Upstream .cceignore only adds exclusions; settings can override defaults.
        return [*excluded, *patterns(project)]
    ignorefile.load_ignore_patterns = merged
    original_ignore = watcher._DebouncedHandler._should_ignore
    def should_ignore(self, path):
        if original_ignore(self, path):
            return True
        try:
            relative = Path(path).relative_to(self._watch_dir).as_posix()
        except ValueError:
            return True
        return ignorefile.matches_any(relative, False, merged(self._watch_dir))
    watcher._DebouncedHandler._should_ignore = should_ignore

    if hashlib.sha256(Path(pipeline.__file__).read_bytes()).hexdigest() != PIPELINE_PIN:
        raise RuntimeError('Unsupported CCE pipeline source')
    source = textwrap.dedent(inspect.getsource(pipeline._run_indexing_locked))
    if source.count('_BATCH = 50') != 1:
        raise RuntimeError('Unsupported CCE batch definition')
    namespace = dict(pipeline.__dict__)
    exec(compile(source.replace('_BATCH = 50', '_BATCH = 8'),
                 str(pipeline.__file__) + ' [source-first batches]', 'exec'), namespace)
    pipeline._run_indexing_locked = namespace['_run_indexing_locked']
    walk = pipeline._iter_project_files
    code = {'.py','.js','.ts','.tsx','.jsx','.rs','.go','.c','.cpp','.h','.cs','.java','.lua','.css','.html','.toml','.yaml','.yml'}
    def prioritized(root, *args, **kwargs):
        def order(path):
            rel = path.relative_to(root)
            try:
                size = path.stat().st_size
            except OSError:
                size = float('inf')  # upstream safely skips files deleted during scanning
            return ('results' in rel.parts, path.suffix not in code, size, rel.as_posix())
        yield from sorted(walk(root, *args, **kwargs), key=order)
    pipeline._iter_project_files = prioritized
    # Compiled function globals are private: bind the same prioritized iterator.
    namespace['_iter_project_files'] = prioritized

    # Environment-only ORT caps leave FastEmbed SessionOptions at zero (auto).
    # Set the constructor argument that actually reaches both ORT thread pools.
    original_model = fastembed.TextEmbedding
    class BoundedTextEmbedding(original_model):
        def __init__(self, *args, **kwargs):
            if len(args) < 3:
                kwargs.setdefault('threads', threads)
            super().__init__(*args, **kwargs)
    fastembed.TextEmbedding = BoundedTextEmbedding
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    return {'exclude_patterns': excluded, 'exclude_directories': ignored_dirs, 'threads': threads,
            'file_batch_size': 8, 'storage': str(storage)}
