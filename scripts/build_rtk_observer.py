"""Build additive observation against exact RTK 0.48.0; never edit installed RTK."""
import os
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "app/codex_token_saver/assets"
PIN = "fde0a8f185945556f51718de0f4c430bb62b3df6"


def replace(root, file, old, new, count=1):
    path = root / file
    text = path.read_text(encoding="utf-8")
    if text.count(old) != count:
        raise RuntimeError(f"Unexpected source boundary: {file}")
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def patch(source):
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() != PIN:
        raise RuntimeError("RTK source pin mismatch")
    if (subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=source).returncode
            or subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=source, text=True).strip()):
        raise RuntimeError("Refusing to patch modified source; use a clean isolated checkout")
    shutil.copyfile(ASSETS / "rtk_observer.rs", source / "src/core/ces_observer.rs")
    replace(source, "src/main.rs", "fn main() {", "fn main() {\n    core::ces_observer::initialize();")
    replace(source, "src/core/mod.rs", "pub mod tracking;", "pub mod tracking;\npub mod ces_observer;")
    replace(source, "src/core/stream.rs", "    Ok(StreamResult {\n        exit_code,",
        '    super::ces_observer::capture(&raw_stdout, &raw_stderr, "stream.capture");\n\n    Ok(StreamResult {\n        exit_code,')
    old = '''    Ok(CaptureResult {
        stdout: super::utils::decode_process_output(&output.stdout),
        stderr: super::utils::decode_process_output(&output.stderr),
        exit_code,
    })'''
    new = '''    let result = CaptureResult {
        stdout: super::utils::decode_process_output(&output.stdout),
        stderr: super::utils::decode_process_output(&output.stderr),
        exit_code,
    };
    super::ces_observer::capture(&result.stdout, &result.stderr, "exec_capture");
    Ok(result)'''
    replace(source, "src/core/stream.rs", old, new)
    stream_path = source / "src/core/stream.rs"
    stream_text = stream_path.read_text(encoding="utf-8")
    stream_text, number = re.subn(r"(?m)^(\s*)if (raw_(?:stdout|stderr|err)\.len\(\) \+ line\.len\(\)) < RAW_CAP \{",
        r"\1if \2 >= RAW_CAP { super::ces_observer::incomplete(); }\n\1if \2 < RAW_CAP {", stream_text)
    if number != 5:
        raise RuntimeError(f"Expected 5 raw-cap boundaries, found {number}")
    stream_path.write_text(stream_text, encoding="utf-8", newline="\n")
    signature = "    pub fn track(&self, original_cmd: &str, rtk_cmd: &str, input: &str, output: &str) {"
    replace(source, "src/core/tracking.rs", signature,
            signature + "\n        super::ces_observer::observe(input, output);")
    old = '    let raw = format!("{}\\n{}", result.stdout, diff_result.stdout);\n    let shown = never_worse(&raw, &printed);'
    replace(source, "src/cmds/git/git.rs", old,
            old + '\n    crate::core::ces_observer::observe(&diff_result.stdout, shown);')
    old = "    let shown = never_worse(&raw, &rtk_output);"
    replace(source, "src/cmds/system/read.rs", old,
            '    crate::core::ces_observer::register(&content, "read.actual-content");\n' + old +
            '\n    if line_numbers { crate::core::ces_observer::observe(&content, shown); }', count=2)
    old = "                let success = output.status.success();"
    replace(source, "src/main.rs", old,
            '                core::ces_observer::register(&combined_raw, "toml.actual-capture");\n' + old)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--base-binary", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_relative_to(ROOT / ".work"):
        raise RuntimeError("Build only in this project's isolated test runtime")
    patch(source)
    env = os.environ.copy()
    cargo_home = Path(env.get("CARGO_HOME", Path.home()/".cargo"))
    env["RUSTFLAGS"] = f"--remap-path-prefix={source}=rtk --remap-path-prefix={cargo_home}=cargo"
    subprocess.run(["cargo", "build", "--release", "--locked"], cwd=source, check=True, env=env)
    suffix = ".exe" if sys.platform == "win32" else ""
    target = ASSETS / "rtk-observer"
    target.mkdir(exist_ok=True)
    shutil.copyfile(source / f"target/release/rtk{suffix}", target / f"rtk{suffix}")
    patch_bytes = subprocess.check_output(["git", "diff", "--no-ext-diff"], cwd=source)
    (target / "source.patch").write_bytes(patch_bytes)
    shutil.copyfile(source / "LICENSE", target / "LICENSE")
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    record = {"protocol": "ces-rtk-pair-v1", "upstream_version": "0.48.0", "upstream_sha": PIN,
        "platform": sys.platform, "base_binary_sha256": sha(args.base_binary),
        "binary": f"rtk{suffix}", "binary_sha256": sha(target / f"rtk{suffix}"),
        "observer_source_sha256": sha(ASSETS / "rtk_observer.rs"),
        "patch_sha256": sha(target / "source.patch"),
        "note": "Locally built official source with additive observation; not an unmodified official binary."}
    (target / "manifest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
