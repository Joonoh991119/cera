---
description: "Import a .excalidraw, .excalidrawlib or image file into a board"
argument-hint: "<id> <file> [--new [--title T] | --board ID] [--at x,y]"
---
Follow the `tempboard` skill. Arguments: $ARGUMENTS

1. If no site id is given, show the open sites with `tempboard list` and let the user pick one. If no file path is given, ask for it.
2. If neither `--new` nor `--board` is given, ask whether to merge into the current board or open the file as a new board (a merge shows up on everyone's screen immediately).
3. Run `tempboard import <id> <file> [options] --json` (if `tempboard` is not on PATH, use `python3 "${CLAUDE_PLUGIN_ROOT}/bin/tempboard"`), then report how many elements were added, what was skipped, and the board link.
