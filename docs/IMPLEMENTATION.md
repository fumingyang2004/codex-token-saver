# Beta implementation checklist

- [ ] Extract RTK/C​​CE observers and native usage, preserving counting boundaries.
- [ ] Install-once Codex hooks and MCP; ownership, backup, disable/uninstall.
- [ ] Ledger-backed session selection, CLI and localhost dashboard.
- [ ] Windows production session with both components and real dashboard capture.
- [ ] Relocated Windows installer/doctor/UI/uninstall smoke.
- [ ] Linux CI/install/package verification.
- [ ] Pinned, path-clean Windows/Linux artifacts and private GitHub prerelease.

Only RTK, CCE, observed savings and local UI belong in this product.
Official integration reference: https://learn.chatgpt.com/docs/hooks and
https://learn.chatgpt.com/docs/extend/mcp?surface=cli .
