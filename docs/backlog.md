# Backlog

Work toward "rough sketch → concrete with your agent". Branch `feat/rough-to-concrete`.

## Done on the branch (not released yet)

- Tab render queue: open browser tabs render snapshots, LaTeX math and mermaid for the agent, using lease tokens.
- MCP basics: element positions in `board_read`, undirected lines and arrow heads, `board_wait` waits until people stop drawing, agent cursor cleared on exit.
- Opt-in shape snapping: pause at the end of a hand-drawn rectangle, ellipse or diamond to turn it into a clean shape. Off by default.
- `board_snapshot` / `board_selection`: the agent can see the board (or a region or the selection) as a PNG, rendered by an open tab or by the server as a fallback. *(work in progress: round-2 review still has open blocking items — see the next item)*
- Dependency fixes (npm audit 0) and Node 24 CI actions.

## Next (planned)

0. **Finish `board_snapshot`** — close the remaining round-2 review findings (image size caps, cropping, timeouts) and pass a third review round.
1. **Write structure** — `board_add` with `kind:"flow"` (server-side layout, undirected edges), `kind:"math"` (LaTeX typeset by a tab), `kind:"mermaid"`; export a board as mermaid or `.drawio`.
2. **"Ask the agent"** — select strokes and press a button; the request is visible to everyone; `board_requests` / `board_request_update` for the agent. Edit-permission only, short note, rate limits.
3. **Proposal card** — the agent's result appears next to the original with *Replace* / *Keep both* / *Discard*; the original is never deleted silently.
4. **Skill, hook and docs** — the rough → concrete procedure in the plugin skill, a prompt hook for pending requests, end-to-end measurement on a fixed sketch set.
5. **Human UX check** before merging and releasing.

## Known limits

- Shape snapping: the first letter of a line written on its own (ㅇ, a one-stroke ㅁ) can turn into a shape. Off by default; *Keep as ink* undoes it.
- Snapshots: if only a hidden or phone tab is open, the agent waits up to 6 s before the server draws the image. Regions with rotated lines are drawn by the server.
- Render queue: in rare timeouts a mermaid diagram can be inserted twice.
- Antigravity install and MCP image results in Codex are untested on a real client.

## Later / maybe

- On-device handwriting-to-text (macOS Vision) as a no-agent option.
- Numbered marks on snapshots, compact ink points with candidate edges (only if edge recognition falls below 5/6).
- Handwriting transcripts and board search after meetings.
- Strokes that snap into connected arrows; a browser "make this a diagram" button; auto layout button (dagre).
- Word-level uncertainty marks, phone swipe compare.
- Function plots (`kind:"plot"`).
- MCP resources, newer MCP protocol version, Claude Code channels.
- Browser OCR for Linux hosts without an agent.
