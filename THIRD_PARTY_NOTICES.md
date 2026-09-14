# Third-party notices

- RTK 0.48.0, commit `fde0a8f185945556f51718de0f4c430bb62b3df6`: Apache-2.0.
  Source: https://github.com/rtk-ai/rtk/tree/v0.48.0
  The archive includes a modified RTK binary with additive observation hooks.
  The added Rust source, exact patch, provenance manifest and license are in
  `codex_token_saver/assets`. Original RTK is downloaded separately with SHA-256
  verification and remains the fallback. See `licenses/rtk-Apache-2.0.txt`.
- Code Context Engine 0.4.26: MIT.
  Source: https://github.com/elara-labs/code-context-engine
  Installed from PyPI, with dependencies pinned in `requirements.lock`.
  Observation is an additive, exact-source-guarded in-memory patch; installed
  upstream files remain unchanged. See `licenses/cce-MIT.txt`.
- uv 0.8.22: MIT OR Apache-2.0, https://github.com/astral-sh/uv/tree/0.8.22.
  Downloaded only when provisioning Python is necessary, with pinned SHA-256.
  uv downloads CPython 3.13.7 from python-build-standalone using its pinned build
  metadata. Python retains its own licenses in the installed runtime.
- Other Python dependencies are installed from PyPI, not redistributed in this
  archive. Their licenses remain in their distribution metadata. The local
  FastEmbed model is downloaded by CCE on first use and retains upstream terms.

No endorsement by OpenAI, RTK, or CCE is implied.

