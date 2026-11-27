---
description: "Stop a temporary whiteboard site (links stop working, drawings are kept)"
argument-hint: "<id> | --all"
---
Arguments: $ARGUMENTS. If empty, first show the open sites with `tempboard list` and ask which one to close. Then run `tempboard stop <id|--all>` (if `tempboard` is not on PATH, use `python3 "${CLAUDE_PLUGIN_ROOT}/bin/tempboard"`).
Use `delete`, which also deletes the drawings, only when the user explicitly asks to delete.
