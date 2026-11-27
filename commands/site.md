---
description: "Personal always-on board on your own domain (named tunnel, optional Cloudflare Access): guide, setup and checks"
argument-hint: "[guide | init --hostname board.<domain> | run | install --load | status | doctor | links]"
---
Follow the `cera` skill. Arguments: $ARGUMENTS (the CLI is `cera site …`; if `cera` is not on PATH, use `python3 "${CLAUDE_PLUGIN_ROOT}/bin/cera" site …`)

1. With no arguments, check `cera site status`. If there is no personal site, show `cera site guide` and relay the steps the person must do by hand in the same order (put the domain on Cloudflare → `cloudflared tunnel login` → (optional) Access application, policy, copy the AUD tag). The person does the logins and dashboard work.
2. `init` and `install --load` change Cloudflare DNS and the login service, so confirm with the user once before running them. If `init` exits with code 2, relay the printed to-do list as is.
3. If something is wrong, run `cera site doctor` and show the FAIL lines with their suggested fixes.
4. Links come from `cera site links`. In Access mode, tell the user that the public address has no key and is entered through the Access login.
