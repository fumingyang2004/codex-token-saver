"""Build a platform archive from an explicit allowlist, then scan its payload."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = (ROOT/'VERSION').read_text().strip()
    platform = 'windows-x64' if sys.platform == 'win32' else 'linux-x64'
    name = f'codex-token-saver-v{version}-{platform}'
    (ROOT/'.work').mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=name+'-',dir=ROOT/'.work'))
    wheels = stage/'wheels'; wheels.mkdir()
    subprocess.run([sys.executable,'-m','pip','wheel','--no-deps','--wheel-dir',str(wheels),str(ROOT)],check=True)
    for file in ('install.ps1','install.sh','uninstall.ps1','uninstall.sh','README.txt','VERSION','requirements.lock','THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT/file,stage/file)
    (stage/'scripts').mkdir()
    shutil.copy2(ROOT/'scripts/bootstrap.py',stage/'scripts/bootstrap.py')
    shutil.copytree(ROOT/'licenses',stage/'licenses')
    for p in stage.glob('*.sh'): p.chmod(0o755)
    # Scan expanded wheels too: archives must not hide developer paths or secrets.
    forbidden = [str(ROOT).encode().lower(), str(Path.home()).encode().lower(), b'lab0913', b'ff966', b'auth.json']
    def inspect(name, data):
        for pattern in forbidden:
            if pattern in data.lower() or pattern.decode().encode('utf-16le') in data.lower():
                raise SystemExit('Private developer marker in release payload: '+name)
    for p in stage.rglob('*'):
        if p.is_file():
            if p.suffix == '.whl':
                with zipfile.ZipFile(p) as z:
                    for n in z.namelist(): inspect(n,z.read(n))
            else: inspect(str(p.relative_to(stage)),p.read_bytes())
    dest = ROOT/'dist'; dest.mkdir(exist_ok=True)
    if sys.platform == 'win32':
        archive = Path(shutil.make_archive(str(dest/name),'zip',stage))
    else:
        archive = dest/(name+'.tar.gz')
        with tarfile.open(archive,'w:gz') as z:
            for p in stage.iterdir(): z.add(p,arcname=p.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dest/(name+'.sha256')).write_text(digest+'  '+archive.name+'\n')
    print(archive.name, digest)


if __name__ == '__main__': main()
