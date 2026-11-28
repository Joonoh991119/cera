# Backlog

Work toward "rough sketch → concrete with your agent". Branch `feat/rough-to-concrete`.

## Done on the branch (not released yet)

- Tab render queue: open browser tabs render snapshots, LaTeX math and mermaid for the agent, using lease tokens.
- MCP basics: element positions in `board_read`, undirected lines and arrow heads, `board_wait` waits until people stop drawing, agent cursor cleared on exit.
- Opt-in shape snapping: pause at the end of a hand-drawn rectangle, ellipse or diamond to turn it into a clean shape. Off by default.
- `board_snapshot` / `board_selection`: the agent can see the board (or a region or the selection) as a PNG, rendered by an open tab or by the server as a fallback.
- Dependency fixes (npm audit 0) and Node 24 CI actions.
- Write structure: `board_add` with `kind:"flow"`, `kind:"math"`, `kind:"mermaid"`; export a board as mermaid or `.drawio`.
- "Ask the agent": select strokes and press a button; request chips everyone can see; `board_requests` / `board_request_update`.
- Proposal card: the agent's result appears next to the original with *Replace* / *Keep both* / *Discard* / *Undo replace* (three review rounds, two reviewers, no open blockers or fixes).
- Proposal follow-ups: the card never covers results (its own or another proposal's), fits a 360 px phone, and falls back to a ✦ button; labelled flow edges are long enough to show the arrow; a formula that fails to typeset is not left as a proposal; an agent can take back its own untouched proposal explicitly (`withdraw`).
- Skill, hook and docs: the rough → concrete procedure in the plugin skill and `references/rough-to-concrete.md`, a `UserPromptSubmit` hook (`cera pending --hook`, about 10 ms over interpreter start, no network) that tells the session about waiting requests, a user page with three screenshots, and an end-to-end plumbing test (`tests/live/live_rough.py`). Three review rounds, two reviewers.

## Next (planned)

1. **Smoke and measurement** — four-device smoke, end-to-end measurement on the fixed sketch set (round trip, tool calls, recognition), first-time-user review.
2. **Human UX check** before merging and releasing (real MathJax and mermaid rendering, phone layout at 360–375 px, real iOS keyboard, WebKit and Firefox, exports without proposal cards).
3. **Recognition loop** (proposal, not scoped yet) — one-shot read with kind + text/LaTeX + uncertain spots and alternatives, fix-before-replace on the card, opt-in local correction log, a scoring CLI.

## Known limits

- Shape snapping: the first letter of a line written on its own (ㅇ, a one-stroke ㅁ) can turn into a shape. Off by default; *Keep as ink* undoes it.
- Snapshots: if only a hidden or phone tab is open, the agent waits up to 6 s before the server draws the image. Regions with rotated lines are drawn by the server.
- Render queue: in rare timeouts a mermaid diagram can be inserted twice.
- Antigravity install and MCP image results in Codex are untested on a real client.
- Proposal card (minor, from review): a 409 always shows "someone else pressed first", also when the originals were erased; an earlier toast's timer can hide a later 409 toast; after *Replace*, the author's undo key skips the removed originals and undoes an older stroke; an arrow that was bound to an original is released on *Replace* and re-bound on *Undo replace*, but not re-bound to the result; if a person moves a result, the dashed anchor does not follow; the "changed since read" baseline is kept per request (up to 20,000 ids), which grows board metadata on very large boards; a non-string `frameId` posted directly by an edit connection still makes the proposal action fail without a response.
- Withdrawing a proposal: a formula image that a person only resized (same LaTeX, place and turn) still counts as untouched; if a person starts dragging a result in the same 50 ms an agent withdraws, the dragged element can stay behind without its card; if an agent's session dies while a formula is being typeset and another session proposes again, the request has two cards.
- An undefined LaTeX macro (`\fracc`) is rendered in red by MathJax and is not reported as an error; a real syntax error (`\frac{a`) is.
- The hook needs `python3` on the PATH of the agent's shell; link-mode installs do not register it.
- After an agent's MCP process restarts, a new connection cannot place a proposal on the request the old one held until that request turns "interrupted" (up to 60 s).

## Later / maybe

- On-device handwriting-to-text (macOS Vision) as a no-agent option.
- Numbered marks on snapshots, compact ink points with candidate edges (only if edge recognition falls below 5/6).
- Handwriting transcripts and board search after meetings.
- Strokes that snap into connected arrows; a browser "make this a diagram" button; auto layout button (dagre).
- Word-level uncertainty marks, phone swipe compare.
- Function plots (`kind:"plot"`).
- MCP resources, newer MCP protocol version, Claude Code channels.
- Browser OCR for Linux hosts without an agent.
