---
description: "Open a temporary shared whiteboard site and get invite links"
argument-hint: "[board name] [--ttl 12h] [--view-link] [--lan | --tunnel none]"
---
Follow the `cera` skill. Arguments: $ARGUMENTS

1. Split the arguments into the board name (the words outside the options; may be absent) and the options. If no expiry was given, use the default `--ttl 12h`.
2. Run `cera start --json [--title "<name>"] [options]` (the first run takes a while because it downloads the UI files). If `cera` is not on PATH, use `python3 "${CLAUDE_PLUGIN_ROOT}/bin/cera"`.
3. Show the user `links.edit` (draw together), `links.view` (view only, if present) and the closing time exactly as returned. Also say in one line that anyone with the link can get in.
