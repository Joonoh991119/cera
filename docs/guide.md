# tempboard guide

**A collaborative whiteboard you share with one link — a plugin for Claude Code, OpenAI Codex and Google Antigravity.**

tempboard runs a small whiteboard server on your computer and gives you an invite link. Anyone with the link can draw with you in real time: no accounts, no sign-up. A temporary board sits behind a Cloudflare quick tunnel and closes itself when its time runs out. You can also keep an always-on board at a fixed address on your own domain. Coding agents connect over MCP to read the board and draw on it next to the people using it.

```
/tempboard:open meeting-notes --ttl 2h --view-link

tempboard 1119-1843-fb4f — running · 11/19 20:43 에 닫힘
  같이 그리기  https://xxxx.trycloudflare.com/?k=…#b=…
  보기 전용    https://xxxx.trycloudflare.com/?k=…#b=…
```

(`에 닫힘` = closes at, `같이 그리기` = draw together (edit link), `보기 전용` = view only.)

> **Language note:** the board screen and the CLI / MCP messages can each be Korean or English (see [Language](#language)); the board also ships a set of Korean fonts. Commands, flags and the MCP tool names are always in English. Where this README quotes a UI label, it gives the Korean text with an English gloss.

## Language

CLI, server and MCP messages are available in Korean and English. The language is chosen in this order:

1. the `TEMPBOARD_LANG` environment variable (`ko` or `en`)
2. the choice saved with `tempboard lang ko` or `tempboard lang en` (stored in `$TEMPBOARD_HOME/config.json`)
3. the system locale (`LC_ALL`, `LC_MESSAGES`, `LANG` starting with `ko` means Korean; `C` / `C.UTF-8` count as unset)
4. English

`tempboard lang` with no argument shows the current language and where it came from (env, saved, locale or default); `tempboard lang auto` clears the saved choice. Machine-readable output (`--json` keys, status values) does not change with the language.

The board screen is separate: the board picks Korean or English from your browser language; switch it from the menu.

## Features

- **Excalidraw-based board** with a large toolbar: select, pen, eraser, text, math, shapes, laser pointer, pan.
- **11 Korean fonts**: 고딕 (gothic), 고딕 굵게 (gothic bold), 손글씨 (handwriting), 또박 (neat print), 펜글씨 (pen script), 동글 (rounded), 분필 (chalk), 굵은 제목 (bold heading), 제목 (heading), 명조 (serif), 코드 (code). Preview them in the font panel and set line spacing and alignment. A font is downloaded only when you first use it.
- **Math**: LaTeX rendered with MathJax, with a symbol palette and recent formulas. When you edit a formula it keeps its size, color and attached arrows.
- **Flowcharts**: shapes and elbow arrows, a "+" handle beside a shape to add the next box, alignment tools, and mermaid text converted to a flowchart.
- **Pen assists**: hold at the end of a stroke to get a straight line, smoothing (off / light / strong), pen-only drawing (fingers pan the canvas), highlighter.
- **Pages**: flip through pages, add a blank page, grid background. Export the current page as PNG or all pages as one PDF.
- **Follow mode**: tap a person's chip to follow their view, or use 모두 나에게 (everyone to me). Viewers on a view-only link follow the board's creator automatically.
- **Export and import**: export the whole board, the current page or the selection as PNG/PDF, and boards as `.excalidraw`. Import `.excalidraw`, `.excalidrawlib` and image files by dragging them onto the board, from the CLI or over MCP.
- **Agents over MCP**: a stdio MCP server (`tempboard mcp`) lets Claude Code, Codex and Antigravity read and draw on boards. Agents appear on the board with an "AI" badge, and the server never overwrites a change a person made after the agent read the board.
- **Personal board on your own domain**: an always-on board at `board.<your-domain>` through a Cloudflare named tunnel, optionally behind Cloudflare Access (only the email addresses you allow), restarted automatically after reboot (launchd on macOS, systemd on Linux).
- **Works on phones**: text, including text inside shapes, is typed in an input bar at the bottom that opens the keyboard on tap. A top bar holds the menu, undo, eraser, time left and the participant count.
- **Real-time sync**: WebSocket with a long-polling fallback, live cursors and name tags, per-user undo (you undo only your own changes). Drawings survive a server restart.
- **Invite keys**: separate edit and view-only links. Requests without a key get 403. If a link leaks, `tempboard keys <id> --rotate all` cuts off the old links within 2 seconds.
- **No Python dependencies**: the server, CLI and MCP server use only the Python standard library (3.9+).

## Requirements

- **Python 3.9+** on macOS or Linux.
- **[`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)**, only for public links and the personal board (`brew install cloudflared` on macOS). It is not needed for `--tunnel none` or `--lan`.
- **Network access on first run**: the web UI bundle (about 30 MB) is downloaded from this repository's GitHub Release into `~/.tempboard/dist/<version>/`. If the download fails, tempboard builds the UI itself, which needs **Node.js (npm)** and a writable checkout.
- For the personal board only: a free Cloudflare account and a domain whose DNS is on Cloudflare.

## Install

The same repository is a plugin for all three agents. Each agent reads its own manifest, so the files don't conflict:

| Agent | Manifest |
|---|---|
| Claude Code | `.claude-plugin/plugin.json` (+ `.claude-plugin/marketplace.json`) |
| Codex | `plugin.json` + `mcp.json` (Agent Plugins 1.0) |
| Antigravity | `.antigravity-plugin/plugin.json` + `mcp_config.json` |

All three share the skill in `skills/tempboard/SKILL.md`.

### Claude Code

```
/plugin marketplace add Joonoh991119/tempboard
/plugin install tempboard@tempboard
```

This installs the slash commands `/tempboard:open`, `/tempboard:list`, `/tempboard:stop`, `/tempboard:export`, `/tempboard:import` and `/tempboard:site`, the `tempboard` skill, the `tempboard` MCP server, and puts `tempboard` on your PATH.

### Codex

```bash
codex plugin marketplace add Joonoh991119/tempboard
codex plugin add tempboard@tempboard
```

This installs the `tempboard` skill and the `tempboard` MCP server. Codex does not use the `commands/` folder; the skill covers the same tasks.

### Antigravity

```bash
agy plugin install https://github.com/Joonoh991119/tempboard
```

The Antigravity path follows Antigravity's plugin and MCP documentation but has not been tested end to end. If it doesn't work, use link mode (below).

### One command for all agents: `tempboard install`

From a clone of the repository, the CLI can call each agent's installer for you:

```bash
git clone https://github.com/Joonoh991119/tempboard && cd tempboard
python3 bin/tempboard install --agent all                   # plugin mode, for every agent found (claude, codex, agy)
python3 bin/tempboard install --agent codex --mode link     # development: point at this checkout, no copy
python3 bin/tempboard install --agent all --dry-run         # only show what would be written and run
python3 bin/tempboard uninstall --agent all                 # undo
python3 bin/tempboard install --print-config codex > tb.toml   # print the MCP config snippet only
```

`--source OWNER/REPO[@ref] | <git URL> | <local path>` changes where plugin mode installs from (default `Joonoh991119/tempboard`). `--print-config claude|codex|antigravity` writes nothing: the snippet (JSON or TOML) goes to stdout and the explanation to stderr.

| | plugin mode (default) | link mode (development) |
|---|---|---|
| all agents | — | `~/.local/bin/tempboard` → `<checkout>/bin/tempboard` |
| claude | `claude plugin marketplace add <source>` → `claude plugin install tempboard@tempboard` | `claude mcp add --scope user tempboard -- <python> <checkout>/bin/tempboard mcp` + link `~/.claude/skills/tempboard` |
| codex | `codex plugin marketplace add <source>` → `codex plugin add tempboard@tempboard` | `codex mcp add tempboard -- <python> <checkout>/bin/tempboard mcp` + link `~/.agents/skills/tempboard` |
| antigravity | `agy plugin install <git URL or path>` (instructions only if `agy` is missing) | merge `mcpServers.tempboard` into `~/.gemini/config/mcp_config.json` (backup `.bak-<timestamp>`) + link `~/.gemini/config/skills/tempboard` |

How `install` treats your configuration:

- **It is idempotent.** Running it again gives the same result.
- **It never hand-edits Claude or Codex config files.** MCP servers are registered only through `claude mcp add` and `codex mcp add` (Codex's `config.toml` is backed up first). If a `tempboard` entry already exists and matches, it is left alone. If it differs (another checkout, another Python), **nothing is changed**: the command prints the difference, the one-line command to fix it, and which fields (such as an `env` holding a remote token) would be lost, then exits with code 3.
- **Antigravity's `mcp_config.json` is the only file merged directly**, as JSON: only `command` and `args` change, a timestamped backup is written first, symlinks are followed to the real file, the result is read back to verify, and a file with broken JSON is left untouched.
- `--agent all` skips agents that aren't installed, with one line for each.
- Link mode starts the MCP server with the absolute path of the Python used at install time. If you change or remove that Python (pyenv, Homebrew), `install --mode link` reports the mismatch.
- Installing plugin mode and link mode for the same agent is refused, because the skill and MCP server would appear twice.
- During development, use link mode. Adding a local path as a Codex marketplace makes Codex copy the whole folder, including `.git` and `web/node_modules`.
- Config locations it respects: `CLAUDE_CONFIG_DIR` (default `~/.claude`), `CODEX_HOME` (default `~/.codex`), `~/.gemini/config`, `~/.agents/skills`, `~/.local/bin`.

Exit codes: `0` done · `1` failed · `2` bad arguments · `3` an existing `tempboard` MCP entry differs and was left as is.

## Quick start: a temporary board

Ask your agent in plain language ("open a whiteboard and give me the link", "화이트보드 열어 줘"), use a slash command in Claude Code, or call the CLI directly.

| Claude Code | CLI | What it does |
|---|---|---|
| `/tempboard:open [name] [--ttl 3h] [--view-link]` | `tempboard start …` | Open a new temporary site and get invite links |
| `/tempboard:list [id]` | `tempboard list` · `tempboard links <id>` | List sites · show a site's links again |
| `/tempboard:stop <id>` | `tempboard stop <id>` · `tempboard stop --all` | Close a site (drawings are kept) |
| `/tempboard:export <id>` | `tempboard export <id> [--out DIR]` | Save each board as a `.excalidraw` file |
| `/tempboard:import <id> <file>` | `tempboard import <id> <file> [--new \| --board ID]` | Import a file into a board |
| `/tempboard:site …` | `tempboard site guide\|init\|run\|install\|status\|doctor\|links` | Personal board on your domain |
| | `tempboard keys <id> [--rotate edit\|view\|all]` | Show or rotate invite keys |
| | `tempboard agent add\|list\|revoke <id>` | Agent tokens |
| | `tempboard delete <id> --yes` | Delete a closed site, drawings included |

`tempboard start` options:

| Option | Meaning |
|---|---|
| `--title TITLE` | Name of the first board; the links open that board directly |
| `--ttl 30m\|3h\|2d\|0` | How long the site stays open (default `12h`; `0` = until you stop it) |
| `--view-link` | Also create a view-only link |
| `--tunnel cloudflared\|none` | Public address via a Cloudflare quick tunnel (default, no account needed, `*.trycloudflare.com`), or `none` for this computer only |
| `--lan` | Listen on `0.0.0.0` so people on the same network (Wi-Fi) can join |
| `--name NAME` | Creator name shown for the first board |
| `--port PORT` | Local port |
| `--json` | Machine-readable output |

`tempboard delete` refuses a running site (stop it first) and needs `--yes`.

## Connecting agents

Installing the plugin registers the `tempboard` MCP server (`python3 <plugin>/bin/tempboard mcp`, stdio) automatically. To register it yourself, paste the snippet from `tempboard install --print-config claude|codex|antigravity`.

| Tool | What it does |
|---|---|
| `site_list` · `site_open` · `site_close` | List sites · open one (defaults to `tunnel:"none"`, this computer only) · close one |
| `board_list` · `board_create` | List boards · create one |
| `board_read` | A summary (pages, boxes, text, connections, formulas, handwriting stroke counts, each line ending in its version `(vN · label vM)`; `(vN · 라벨 vM)` in Korean), or raw elements with `format:"elements"`, narrowed by `ids`, `page`, `limit`, `offset` |
| `board_add` | Add text, boxes, arrows, pages and images in one batch. `at`: `"right"` of existing drawings (default), `"below"`, `{x, y}` or `"absolute"` |
| `board_update` | Change text, labels, position, size or color |
| `board_delete` | Delete elements, cleaning up labels and arrow bindings |
| `board_wait` | Wait (up to 60 s) for a person to change the board and return the changes line by line |
| `board_import` · `board_export` | Import `.excalidraw`, `.excalidrawlib` or image files / save a board as `.excalidraw` (default `$TEMPBOARD_HOME/exports`) |

**Version-guarded edits.** Every line of a `board_read` summary ends with `(vN · label vM)`, the element's version and, for labeled shapes, its label's version. `board_update` calls that change text or color must pass `base_version=N`, plus `label_base_version=M` for a labeled box or arrow. Every `board_delete` item must carry both as well (`{id, base_version, label_base_version}`). Versions are plain integers (`2`, not `"v2"`). A missing version returns `refused`. If a person edited the element after the agent read it, the server returns `conflict` with what changed and does not overwrite anything. Moving or resizing alone needs no version. Label text is edited through the box's id; label ids are rejected.

**Agent tokens.**

- **Same computer:** the MCP server creates its own agent token for each site and revokes it when it exits. The agent appears as `Claude · AI` (set the name with `TEMPBOARD_AGENT_NAME`).
- **A board on another computer:** set `TEMPBOARD_URL=<board address>` and `TEMPBOARD_TOKEN=tb_ag_…`, issued with `tempboard agent add <id> --name <name> [--role edit|view] [--ttl 2h]`. The token is shown only once, and by default it expires with the site or after 12 hours, whichever comes first. For a personal board in Access mode, use a Cloudflare Access service token instead: `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET`. In remote mode the `site_*` tools are hidden.
- **Revoking:** `tempboard agent revoke <id> <token-id>` or `tempboard agent revoke <id> --all` cuts the token off within 2 seconds. `tempboard agent list <id>` shows tokens (never their plaintext).

## Importing boards

Supported formats: `.excalidraw`, `.excalidrawlib` and images (PNG, JPEG, GIF, WebP, SVG). Files can be up to 50 MB, and a single image up to about 7.5 MB. Merging into an existing board assigns fresh ids, and the result appears on everyone's screen at once. Importing as a new board keeps the file's ids.

- **In the board UI:** drop a `.excalidraw` or `.excalidrawlib` file on the board and choose 이 보드에 합치기 (merge into this board), 새 보드로 열기 (open as a new board) or 취소 (cancel). A merge from the UI lands in the middle of the current view. The menu item 파일 가져오기… (import file…) does the same on desktop and phone; it is hidden on view-only links. Image files are added directly.
- **CLI:** `tempboard import <site> <file> [--board ID | --new [--title T]] [--at x,y] [--json]`. By default the file is merged into the site's first board, to the right of the existing drawings. `--at` sets the center of the imported group. A running site is updated over HTTP, and a closed site's storage is written directly.
  Examples: `tempboard import <id> notes.excalidraw --new --title "Last week"` · `tempboard import <id> figure.png --board <board-id> --at 0,0`
- **MCP:** `board_import {path, mode: "merge" (default) | "new", site?, board?, title?, at?: "right" | "below" | {x, y}}`. Only regular files on this computer are accepted, not URLs. The result is `{board, added, skipped, …}`.
- **HTTP API** (edit permission): `POST /api/whiteboard/boards/import {name, content, title?}` creates a new board (201). `POST /api/whiteboard/boards/:id/import {name, content, at?}` merges into a board. `content` is the file's JSON text or an image data URL.

## A personal board on your own domain (Cloudflare)

Besides temporary boards, you can keep a board always on at a fixed address such as `https://board.example.com/`. It runs on your computer and reaches the internet through a Cloudflare named tunnel. `tempboard site guide` prints these steps.

**What you do by hand, once, in this order:**

1. **Put your domain on Cloudflare.** At https://dash.cloudflare.com choose Add a domain → Free plan, then enter the two nameservers Cloudflare shows into your registrar's nameserver settings. Continue once Cloudflare shows the domain as Active (minutes to hours).
2. **Log in from this computer:** run `cloudflared tunnel login`, pick the domain in the browser and click Authorize. This creates `~/.cloudflared/cert.pem`. Keep it private.
3. **Run `tempboard site init --hostname board.example.com`.**
4. **Test it:** `tempboard site run` runs the board in the foreground (stop with Ctrl+C). Check that `https://board.example.com/` opens, and run `tempboard site doctor`.
5. **Keep it running:** `tempboard site install --load`.

**What the CLI does for you:**

- `site init` creates the tunnel, routes the DNS record, writes the site's config files and checks the result. It reads `cert.pem` but never touches your global `~/.cloudflared/config.yml`. Without `cert.pem` it prints the remaining steps and exits with code 2. Useful flags: `--port` (default 8790; a free port is picked and reported if it's taken), `--tunnel-name`, `--reuse-tunnel` (take over an existing tunnel with the same name, only when moving machines), `--overwrite-dns`, `--view-link`, `--keep-awake` (`site run` prevents sleep with macOS `caffeinate -s`), `--id` (default `personal`).
- `site run` supervises the server and the `cloudflared` tunnel in the foreground and restarts them indefinitely.
- `site install [--load]` writes a macOS LaunchAgent or Linux systemd user unit that starts at login and restarts on failure. `--load` also loads it with `launchctl` or `systemctl --user`. `--dry-run` shows the file and commands without writing anything.
- `site status`, `site doctor [--offline] [--verbose]` (each line is OK, WARN or FAIL with a suggested fix; exit code 1 if anything fails), `site links`, and `site port [N|auto]` (change only the port when another program needs it; tunnel and keys stay).
- Stopping: Ctrl+C for a foreground `site run`. For the service, `tempboard stop personal` stops it until the next login, and `tempboard site uninstall --load` removes it.

A personal site lives under the same `sites/<id>/` folder as temporary sites, so `tempboard list`, `links`, `keys --rotate`, `stop` and `export` work on it too. After updating the plugin, run `tempboard site install --load` once more so the service picks up the new version.

**Creating the tunnel in the dashboard instead** (remotely managed, no `cert.pem`): in https://one.dash.cloudflare.com go to Networks → Tunnels → Create a tunnel → Cloudflared. Save the token it shows to a file and run `chmod 600` on it (never paste the token on a command line, where `ps` can see it). Set the tunnel's public hostname to `board.example.com` → `http://127.0.0.1:8790`. Then run `tempboard site init --hostname board.example.com --token-file <file>`. To enforce Access at the edge as well, turn on Protect with Access for that route.

**Optional: Cloudflare Access, so only the email addresses you allow can get in** (Zero Trust Free plan, up to 50 users):

- A. The first time you open https://one.dash.cloudflare.com, choose a team name. Your login address becomes `<team>.cloudflareaccess.com`.
- B. Under Settings → Authentication → Login methods, add One-time PIN.
- C. Under Access → Applications, choose Add an application → Self-hosted, enter `board.example.com`, and add a policy with Action Allow and Include Emails (specific addresses) or Emails ending in (a domain such as `@example.org`).
- D. Open the application and copy its Application Audience (AUD) Tag.
- E. Run `tempboard site init --hostname board.example.com --access-team <team> --access-aud <AUD>`. If the service is already running, run `tempboard site install --load` again. (`--no-access` switches back to invite keys.)
- F. Optional, to separate editors from viewers: create `~/.tempboard/sites/personal/members.json`, for example `{"edit": ["me@example.org", "@example.org"], "view": ["guest@example.com"]}`. Without the file, everyone who passes Access can edit. With the file, only the people listed under `edit` or `view` get in (use `"edit": ["*"]` to admit everyone who passes Access). `edit` and `view` must be lists. Changes apply immediately. A malformed file locks everyone out, so check it with `tempboard site doctor`.
- G. Optional, for agents on other machines: create a service token under Access → Service credentials → Service Tokens and keep its Client ID and Secret. Add a second policy to the application from step C with Action Service Auth, Include Service Token = that token. Then list the token in `members.json` together with the people: `{"edit": ["*"], "agents": {"<Client ID>": {"name": "codex@lab-server", "role": "edit"}}}`. If you list only agents, no person can get in. The agent sends the `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers on every request (set `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` for `tempboard mcp`).

Menu names can differ slightly between dashboard versions. In Access mode the public address has no key and opens after the Access login. The local address on this computer (`127.0.0.1`) still opens with the invite key, without Access.

## Security notes

- **Anyone with a link can get in.** An edit link lets people draw and create boards. People who arrive through a view-only link never receive the edit key. If a link leaks, run `tempboard keys <id> --rotate all`. Agent tokens are separate from invite keys and survive a rotation; to cut off agents too, run `tempboard agent revoke <id> --all`.
- The server binds only to `127.0.0.1` and the tunnel connects it to the outside (`0.0.0.0` only with `--lan`). Requests with an unknown `Host` get 421 (DNS-rebinding protection), and WebSocket connections are checked against their `Origin`. Write requests need an `X-Whiteboard` header (CSRF protection), and the Content-Security-Policy is `script-src 'self'`. On public addresses, cookies use the `__Host-` prefix and `Secure`, and the personal board sends HSTS.
- **Agent tokens** are stored only as SHA-256 hashes in `state.json`; the plaintext is shown once, when the token is issued. They expire after 12 hours by default and stop working within 2 seconds of being revoked. In personal and Access mode, agent tokens are accepted only on requests from this computer.
- **In Cloudflare Access mode** the server verifies the `Cf-Access-Jwt-Assertion` itself (RS256 signature, audience, issuer, expiry) rather than trusting an email header. Access mode cannot be combined with `--trust-header`.
- Secrets (invite keys, tokens, tunnel credentials) are kept out of stdout, logs and command-line arguments, and files holding them are mode 0600. A tunnel token is accepted only through `--token-file`. Running `tempboard delete` on a personal site does not delete the tunnel credentials; they are moved to `~/.tempboard/kept/`. The tunnel and DNS record stay on Cloudflare until you remove them (`cloudflared tunnel delete <name>` and the DNS record in the dashboard).
- The MCP tools that open a public tunnel or delete things tell the agent, in their descriptions, to ask you first. The server does not enforce this; tool approval follows each agent's own settings.
- A site stops when your laptop sleeps. Quick tunnels are a Cloudflare trial service with no availability guarantee, and the address can change when the tunnel reopens (`tempboard links <id>` shows the new one). Use a personal board for anything long-lived.

## Data locations

| Path | Contents |
|---|---|
| `~/.tempboard/` | Everything; override with `TEMPBOARD_HOME` |
| `~/.tempboard/sites/<id>/` | One site: drawings in `data/`, `state.json` (0600). A personal site adds `cloudflared.yml`, `tunnel.json` (0600) and optionally `members.json` |
| `~/.tempboard/dist/<version>/` | Downloaded or built web UI |
| `~/.tempboard/exports/` | Default output of the MCP `board_export` tool |
| `~/.tempboard/kept/` | Tunnel credentials moved aside when a personal site is deleted |

`tempboard export` writes to `./tempboard-<id>/` in the current folder unless you pass `--out`.

Environment variables:

| Variable | Purpose |
|---|---|
| `TEMPBOARD_HOME` | Data folder (default `~/.tempboard`) |
| `TEMPBOARD_DIST` | Use this web UI build. Lookup order: `TEMPBOARD_DIST` > the checkout's `web/dist` (if built) > `$TEMPBOARD_HOME/dist/<version>/` |
| `TEMPBOARD_RELEASE_URL` | Download the UI bundle from another base URL (mirror, testing) |
| `TEMPBOARD_AGENT_NAME` | Display name of the MCP agent on the board |
| `TEMPBOARD_URL`, `TEMPBOARD_TOKEN` | Connect `tempboard mcp` to a board on another computer |
| `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` | Cloudflare Access service token for a personal board in Access mode |

## Development

```bash
cd web && npm ci && npm run build && npm test        # build the UI and run the JS tests
python3 -m unittest discover -s tests -q             # server, CLI, MCP and installer tests
python3 packaging/gen.py --check                     # generated manifests match the source (run in CI)
python3 server/server.py --data /tmp/tb --edit-key dev   # → http://127.0.0.1:8790/?k=dev
scripts/release-dist.sh && gh release create v<version> web/tempboard-dist.tar.gz   # release the UI bundle
```

- **The manifest source of truth is `.claude-plugin/plugin.json`.** After changing the version, description or MCP config, run `python3 packaging/gen.py` to regenerate the root `plugin.json` and `mcp.json`, `.antigravity-plugin/plugin.json`, `mcp_config.json` and the version in `marketplace.json`, and commit them together. Don't add a root `.mcp.json` (Claude Code would load it as a project MCP server while you work on this repo) or `.agents/plugins/marketplace.json` (Codex would read it first and the marketplace name would change).
- **After changing dependencies**, regenerate `THIRD_PARTY_NOTICES.md` with `python3 packaging/notices.py`. `gen.py --check` verifies it against `web/node_modules`, and also checks that versions and licenses in `web/package.json` and `web/package-lock.json` match the source.
- The HTTP API of `server/server.py` (boards, element merge, WebSocket, long polling, files, patch, import) is documented in the server's header and code comments. Merging follows Excalidraw's rule: the higher `version` wins, and on a tie the lower `versionNonce` wins.

tempboard started as the team whiteboard for a research-competition wiki and was extracted and generalized here.

## License

MIT — see [LICENSE](../LICENSE). Every manifest (`.claude-plugin/plugin.json`, `plugin.json`, `.antigravity-plugin/plugin.json`, `web/package.json`) declares `"license": "MIT"`, and source files carry `SPDX-License-Identifier: MIT`.

Third-party components: the web bundle includes Excalidraw (MIT) and the fonts it ships (SIL OFL 1.1, MIT), React (MIT), MathJax (Apache-2.0), other npm packages under MIT, ISC, BSD-3-Clause, Apache-2.0 and similar licenses, and Korean font subsets (SIL OFL 1.1). `server/fracindex.py` is ported from rocicorp/fractional-indexing (CC0-1.0). The full list with license texts is in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), generated from `web/package-lock.json` by `python3 packaging/notices.py`.
