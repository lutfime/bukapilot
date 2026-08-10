# kommu-ssh MCP Server — Setup Notes

## CRITICAL: Must be started from the user's Terminal.app, NOT from ZCode

ZCode's bash tool (even with sandbox disabled) does NOT have the same network
routes as the user's terminal. Starting the MCP server from ZCode will fail
with "No route to host". The server MUST be started from Terminal.app where
the user's network/Wi-Fi routes are available.

## Do NOT ask the user about this again. Just try the tools. If they fail, the server needs restarting from terminal.

## How to start (user runs this in Terminal.app):

```bash
SSH_HOST=192.168.0.9 node ~/.zcode/mcp-ssh/server.mjs
```

- Default host: 192.168.0.11 (old), override with SSH_HOST=192.168.0.9
- Default user: kommu
- Default key: ~/lutfime-GitHub
- Default port: 8787
- Leave the terminal window open while working

## After starting, restart ZCode (or reconnect MCP) so tools appear as:
- mcp__kommu-ssh__ssh_exec — run shell command on device
- mcp__kommu-ssh__ssh_get — read file from device
- mcp__kommu-ssh__ssh_put — write file to device
- mcp__kommu-ssh__ssh_info — show SSH target info

## Config location
~/.zcode/cli/config.json → mcp.servers.kommu-ssh
