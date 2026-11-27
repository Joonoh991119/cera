---
name: cera
description: Open, close, export and import link-shared collaborative whiteboards (Cera, formerly tempboard), and let the agent read and draw on a board. Use this skill when the user says things like "open a whiteboard", "share a whiteboard", "temporary whiteboard link", "give me a board link we can draw on together", "draw this on the board", "open this .excalidraw file as a board", "my always-on board on my own domain", or in Korean "화이트보드 열어 줘", "같이 그릴 보드 링크", "회의 중에 그림 같이 그리자", "보드에 그려 줘", "이 .excalidraw 파일 보드로 열어", "내 도메인에 늘 켜 둔 보드". Shared by Claude Code, Codex and Antigravity.
---

# Cera

A whiteboard anyone can join with one link. One site = a small server on this computer + (optionally) a Cloudflare tunnel + invite keys (+ an expiry time).
- **Temporary board**: `cera start` — a Cloudflare quick tunnel (no account needed, `*.trycloudflare.com`), closes after 12 hours by default.
- **Personal board**: `cera site …` — always on at the user's own domain (`board.<domain>`), optionally behind Cloudflare Access (only allowed email addresses).

CLI messages are Korean or English: `CERA_LANG` > `cera lang ko|en|auto` > system locale > English. Pass CLI output to the user as is, and explain it in the user's language if needed.

## How to call it (use the first one available)
1. If the **MCP tools** are available (`site_list`, `site_open`, `board_read`, `board_add`, … see the table below), use them. They work regardless of shell or sandbox.
2. Otherwise run **`cera <command>`** in the shell (on PATH — Claude Code adds the plugin's `bin/`, and `cera install --mode link` creates `~/.local/bin/cera`).
3. If that is missing too, run **`../../bin/cera`** relative to the folder containing this SKILL.md with `python3` (in a plugin install, `bin/` sits next to `skills/`). If this folder is a symlink (such as `~/.claude/skills/cera` in link mode), resolve it with `realpath` first.

Below, `tb` = the CLI found above.

## Commands
| Task | CLI | MCP tool |
|---|---|---|
| Open a temporary board | `tb start --json [--title NAME] [--ttl 12h] [--view-link] [--lan] [--tunnel none]` | `site_open` (defaults to `tunnel:"none"`) |
| List sites · show links again | `tb list` · `tb links <id>` | `site_list` |
| Close (drawings are kept) | `tb stop <id>` · `tb stop --all` | `site_close` |
| Export | `tb export <id> [--out DIR]` → `.excalidraw` | `board_export` |
| Import | `tb import <id> <file> [--board ID \| --new [--title T]] [--at x,y] [--json]` | `board_import` |
| Delete (closed sites only) | `tb delete <id> --yes` | — |
| Rotate invite keys | `tb keys <id> --rotate edit\|view\|all` | — |
| Agent tokens | `tb agent add <id> --name N [--role edit\|view] [--ttl 2h]` · `agent list <id>` · `agent revoke <id> <token-id>\|--all` | — |
| Read and draw on a board | — | `board_list` · `board_create` · `board_read` · `board_add` · `board_update` · `board_delete` · `board_wait` |
| Personal board | `tb site guide` · `site init --hostname board.<domain> [--access-team T --access-aud A]` · `site run` · `site install [--load]` · `site status` · `site doctor` · `site links` | — |
| Attach to an agent | `tb install --agent claude\|codex\|antigravity\|all [--mode plugin\|link]` · `tb uninstall …` · `tb install --print-config <agent>` | — |

Import formats: `.excalidraw`, `.excalidrawlib` and images (png, jpeg, gif, webp, svg). People can also drag a file onto the board or use the menu item 파일 가져오기… (import file…).

## Drawing on a board (MCP)
- Start with `board_read` (summary) to see what is there. Use `format:"elements"` only when you need raw elements, and narrow it with `page`, `limit` and `offset`.
- Other people may be looking at the same board. Everything you draw or change shows up on their screens immediately. Ask before deleting or moving a person's drawing.
- Each summary line ends with `(vN · 라벨 vM)` (라벨 = label): that element's version. `board_update` calls that change text or color, and every `board_delete`, **must** pass `base_version=N`, plus `label_base_version=M` for a labeled box or arrow. If a version is missing or not an integer (`2`, not `"v2"`), the call returns `refused`. Moving or resizing alone needs no version.
- Edit label text through the **box id**. Label ids (`<box-id>-label`) are rejected (`refused`).
- `conflict` means a person edited the element after you read it. The result says what changed (box or label, current version and text). Do not overwrite it: tell the user, read again, then decide.
- Do not create formulas or mermaid flowcharts over MCP. Point the person to the formula and flowchart tools in the board UI.
- To wait for a person's next move, use `board_wait`.
- Never use the invite key (`?k=`) directly. The MCP server creates its own token and revokes it when it exits. An agent on another computer connects with `CERA_URL` + `CERA_TOKEN` (or the Access service token `CF_ACCESS_CLIENT_ID` + `CF_ACCESS_CLIENT_SECRET`).

## Rules
- **Ask the user first** before: opening a public address (the default quick tunnel of `start`, or `site_open` with `tunnel:"cloudflared"`), deleting (`delete`, `board_delete`, editing a person's drawing), issuing a token (`agent add`), the personal board's `site init` and `site install --load` (they leave traces in Cloudflare DNS and the login service), and `install`/`uninstall` (they change the user's agent configuration). If the user clearly said "open a board and give me the link", that is the confirmation: open it right away and, together with the link, say in one line that anyone with the link can get in (edit link = can draw, view link = view only).
- Open boards the agent uses on its own (for example, to explain something with a drawing) with `tunnel:"none"` (this computer only).
- Default expiry is 12 hours. "Just today" → match the time left in the day; "a one-hour meeting" → leave some slack, e.g. `--ttl 90m`. Use `--ttl 0` only when the user wants no expiry.
- Do not send links or tokens to other people (email, chat). Give them to the user, and the user sends them. Do not copy tokens or keys into logs or files.
- If the user doesn't want public access, use `--lan` (same Wi-Fi) or `--tunnel none` (this computer only).
- When the laptop sleeps or shuts down, the site stops. Drawings stay in `$CERA_HOME/sites/<id>/data` (default `~/.cera`). A reopened quick tunnel may get a new address — give the new link with `tb links <id>`.
- If the error says `cloudflared` is missing, tell the user how to install it (`brew install cloudflared`), and install it only with the user's consent.
- On first run, the web UI files (about 30 MB) are downloaded from the GitHub Release into `$CERA_HOME/dist/<version>/`. If that fails, they are built with Node.js automatically.
- For the personal board, `tb site guide` lists what the person must do by hand, in order (domain → `cloudflared tunnel login` → Access application). Relay it as is, and check the setup with `tb site doctor`.
