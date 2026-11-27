# SPDX-License-Identifier: MIT
"""Cera site — personal board web: keep your own domain (board.<your-domain>) open at the same address all the time.

    cera site guide                         # the steps a human must do (domain -> login -> Access)
    cera site init --hostname board.example.com [--access-team T --access-aud A] [--token-file F] [--keep-awake]
    cera site run                           # foreground supervision (server + cloudflared named tunnel, reopened without limit)
    cera site install [--dry-run] [--load]  # keep it always on (macOS LaunchAgent, Linux systemd user unit)
    cera site uninstall [--dry-run] [--load]
    cera site port [N|auto]                 # change only the port (when another program is using it)
    cera site status | doctor [--verbose] | links

Temporary boards (`cera start`) are unchanged. A personal site keeps state.json (mode "personal",
expires_at 0), cloudflared.yml, tunnel.json (tunnel credentials, 0600), and (optionally) members.json under the same `sites/<id>/`, so
`cera list`, `links`, `keys --rotate`, `stop` and `export` keep working. The global `~/.cloudflared/config.yml` is left untouched
(cert.pem is only read). The server and supervise loop are the ones in server.py and supervise.py (this file only handles the CLI and config files).
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import errno
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional
import importlib.util


def _sibling(name):
    key = "cera_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().parent / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


T = _sibling("i18n").T

DEFAULT_ID = "personal"
DEFAULT_PORT = 8790
RESTART_WINDOW = 15 * 60   # doctor: WARN if the tunnel was reopened more than 3 times within this window
RESTART_WARN = 3
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
TUNNEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SYS_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
HERE = Path(__file__).resolve().parent


def _cf_mod(ctx):
    return ctx.load_module("cfaccess", HERE / "cfaccess.py")


# ------------------------------------------------------------------ what a human must do (docs, doctor, and init print the same text)
GUIDE_DOMAIN = T("""\
사람이 할 일 — 처음 한 번, 이 순서로:
  1. 도메인을 Cloudflare 에 올린다. https://dash.cloudflare.com → 도메인 추가(Add a domain) → Free 요금제 →
     안내된 네임서버 2개를 도메인 등록기관(가비아 · 후이즈 · Namecheap 등)의 '네임서버' 칸에 넣는다.
     반영에 몇 분–몇 시간. Cloudflare 에서 그 도메인이 'Active' 가 되면 다음으로.
  2. 이 컴퓨터에서:  cloudflared tunnel login
     브라우저가 열리면 Cloudflare 에 로그인 → 1의 도메인을 고르고 'Authorize'.
     끝나면 ~/.cloudflared/cert.pem 이 생긴다(이 파일로 터널을 만든다 — 남에게 주지 않는다).
  3. 다시:  cera site init --hostname board.<내도메인>
     (터널 만들기 · DNS 연결 · 설정 파일 · 점검은 Cera 가 한다)
  4. cera site run  으로 띄워 https://board.<내도메인>/ 이 열리는지 본다.  cera site doctor 로 점검.
  5. 늘 켜 두기:  cera site install --load   (로그인할 때 자동으로 켜지고, 죽으면 다시 띄운다)
  {stop}
""", """\
What you do by hand, once, in this order:
  1. Put your domain on Cloudflare. https://dash.cloudflare.com -> Add a domain -> Free plan ->
     enter the two nameservers it shows into the nameserver settings of your registrar (GoDaddy, Namecheap, etc.).
     It takes minutes to hours. Continue once Cloudflare shows the domain as 'Active'.
  2. On this computer:  cloudflared tunnel login
     When the browser opens, log in to Cloudflare -> pick the domain from step 1 and click 'Authorize'.
     This creates ~/.cloudflared/cert.pem (it is used to create the tunnel -- keep it private).
  3. Then:  cera site init --hostname board.<your-domain>
     (Cera creates the named tunnel, routes the DNS record, writes the config files and checks the result)
  4. Start it with cera site run and check that https://board.<your-domain>/ opens.  Check it with cera site doctor.
  5. Keep it running:  cera site install --load   (always-on service: starts at login and restarts if it dies)
  {stop}
""")

GUIDE_TOKEN = T("""\
대시보드로 터널을 만들 때(원격 관리 · cert.pem 없이):
  1. https://one.dash.cloudflare.com → Networks → Tunnels → Create a tunnel → Cloudflared → 이름 cera.
  2. 보이는 토큰을 파일에 저장하고 권한을 줄인다:  chmod 600 <파일>   (토큰을 명령줄에 붙이지 않는다 — ps 에 보인다)
  3. 그 터널의 Public Hostname(Published application): board.<내도메인> → 서비스 http://127.0.0.1:{port}
  4. cera site init --hostname board.<내도메인> --token-file <파일>
  (Access 를 앞단에서도 강제하려면 3의 경로에서 'Protect with Access' 를 켠다)
""", """\
To create the tunnel in the dashboard instead (remotely managed, no cert.pem):
  1. https://one.dash.cloudflare.com -> Networks -> Tunnels -> Create a tunnel -> Cloudflared -> name it cera.
  2. Save the token it shows to a file and restrict its permissions:  chmod 600 <file>   (never paste the token on a command line -- ps shows it)
  3. For that tunnel's Public Hostname (Published application): board.<your-domain> -> service http://127.0.0.1:{port}
  4. cera site init --hostname board.<your-domain> --token-file <file>
  (To enforce Access at the edge as well, turn on 'Protect with Access' for the route in step 3)
""")

GUIDE_ACCESS = T("""\
(선택) Cloudflare Access — 정해 둔 이메일만 들어오게(Zero Trust Free, 50명까지):
  A. https://one.dash.cloudflare.com 을 처음 열면 팀 이름을 정한다 → 로그인 주소가 <팀>.cloudflareaccess.com 이 된다.
  B. Settings → Authentication → Login methods 에 'One-time PIN' 을 더한다(이메일로 1회용 번호).
  C. Access → Applications → Add an application → Self-hosted → 도메인 board.<내도메인> →
     정책: Action 'Allow', Include 'Emails'(들어올 주소) 또는 'Emails ending in'(@example.org) → 저장.
  D. 만든 앱을 열어 'Application Audience (AUD) Tag' 를 복사한다.
  E. cera site init --hostname board.<내도메인> --access-team <팀> --access-aud <AUD>
     이미 늘 켜 두기로 돌고 있으면  cera site install --load  (내렸다 다시 올린다).
  F. (선택) 편집 · 보기를 나누려면 {members} 를 만든다:
       {{"edit": ["me@example.com", "@example.org"], "view": ["guest@gmail.com"]}}
     파일이 없으면 Access 를 통과한 사람은 모두 편집이다. 파일을 만들면 edit · view 에 적힌 사람만 들어온다
     (Access 를 통과한 모두를 그대로 들이려면 "edit": ["*"]). edit · view 는 꼭 [ ] 목록으로 쓴다.
     고치면 곧바로 먹는다(다시 켤 필요 없음). 형식이 틀리면 아무도 못 들어오니 cera site doctor 로 확인한다.
  G. (선택) 다른 기계의 에이전트: Access → Service credentials → Service Tokens → Create → Client ID · Secret 을 받아 둔다 →
     C 의 앱에 정책을 하나 더: Action 'Service Auth', Include 'Service Token' = 그 토큰 →
     {members} 를 이렇게(사람 칸도 함께 — 에이전트만 적으면 사람이 아무도 못 들어온다):
       {{"edit": ["*"], "agents": {{"<Client ID>": {{"name": "codex@lab-server", "role": "edit"}}}}}}
     ("*" 대신 F 처럼 이메일 목록을 써도 된다.) 에이전트는 요청마다 CF-Access-Client-Id · CF-Access-Client-Secret 머리를 보낸다.
  ※ 메뉴 이름은 대시보드 판에 따라 조금 다를 수 있다. 이 맥(127.0.0.1)에서 여는 주소는 Access 없이 초대 키로 열린다.
""", """\
(Optional) Cloudflare Access -- only the email addresses you allow can get in (Zero Trust Free, up to 50 users):
  A. The first time you open https://one.dash.cloudflare.com, choose a team name -> your login address becomes <team>.cloudflareaccess.com.
  B. Under Settings -> Authentication -> Login methods, add 'One-time PIN' (a one-time code sent by email).
  C. Under Access -> Applications -> Add an application -> Self-hosted -> domain board.<your-domain> ->
     policy: Action 'Allow', Include 'Emails' (the addresses to admit) or 'Emails ending in' (@example.org) -> save.
  D. Open the application and copy its 'Application Audience (AUD) Tag'.
  E. cera site init --hostname board.<your-domain> --access-team <team> --access-aud <AUD>
     If the always-on service is already running, run  cera site install --load  (it is taken down and brought up again).
  F. (Optional) To separate editors from viewers, create {members}:
       {{"edit": ["me@example.com", "@example.org"], "view": ["guest@gmail.com"]}}
     Without the file, everyone who passes Access can edit. With the file, only the people listed under edit or view get in
     (use "edit": ["*"] to admit everyone who passes Access). edit and view must be written as [ ] lists.
     Changes apply immediately (no restart needed). A malformed file locks everyone out, so check it with cera site doctor.
  G. (Optional) For agents on other machines: Access -> Service credentials -> Service Tokens -> Create -> keep the Client ID and Secret ->
     add a second policy to the application from C: Action 'Service Auth', Include 'Service Token' = that token ->
     write {members} like this (list the people too -- if you list only agents, no person can get in):
       {{"edit": ["*"], "agents": {{"<Client ID>": {{"name": "codex@lab-server", "role": "edit"}}}}}}
     (You can use an email list as in F instead of "*".) The agent sends the CF-Access-Client-Id and CF-Access-Client-Secret headers with every request.
  Note: menu names can differ slightly between dashboard versions. The address opened on this Mac (127.0.0.1) opens with the invite key, without Access.
""")


def stop_howto(sid=DEFAULT_ID) -> str:
    """How to turn it off — guide, `site run --help`, and the first line of run all print the same text."""
    return (T(f"끄기: 전경으로 띄운 site run 은 Ctrl+C · 늘 켜 두기(서비스)는 cera stop {sid}(다음 로그인 때 다시 켜짐) "
            f"또는 cera site uninstall --load{'' if sid == DEFAULT_ID else ' --id ' + sid}(아주 끄기)", f"To stop: a foreground site run with Ctrl+C - the always-on service with cera stop {sid} (it starts again at the next login) "
            f"or cera site uninstall --load{'' if sid == DEFAULT_ID else ' --id ' + sid} (to remove it for good)"))


def guide_text(ctx=None, sid=DEFAULT_ID, port=DEFAULT_PORT) -> str:
    members = str(ctx.SITES / sid / "members.json") if ctx else f"~/.cera/sites/{sid}/members.json"
    return "\n".join([GUIDE_DOMAIN.format(stop=stop_howto(sid)), GUIDE_TOKEN.format(port=port), GUIDE_ACCESS.format(members=members)])


# ------------------------------------------------------------------ checks and paths
def check_id(sid: str) -> str:
    if not ID_RE.match(sid or ""):
        raise SystemExit(T(f"--id 는 영문 소문자 · 숫자 · '-' 로 32자까지: {sid!r}", f"--id: lowercase letters, digits and '-', up to 32 characters: {sid!r}"))
    return sid


def check_hostname(h: str) -> str:
    v = str(h or "").strip().lower().rstrip(".")
    v = re.sub(r"^https?://", "", v).split("/")[0]
    labels = v.split(".")
    if (len(labels) < 3 or len(v) > 253 or not all(LABEL_RE.match(x) for x in labels) or labels[-1].isdigit()
            or v.endswith(".trycloudflare.com") or v.endswith(".cloudflareaccess.com")):
        raise SystemExit(T(f"--hostname 을 읽지 못했다: {h!r} — 내 도메인의 서브도메인 하나(예: board.example.com)", f"Could not read --hostname: {h!r} - give one subdomain of your own domain (e.g. board.example.com)"))
    return v


def check_port(p) -> int:
    try:
        n = int(p)
    except (TypeError, ValueError):
        n = 0
    if not 1024 <= n < 65536:
        raise SystemExit(T(f"--port 는 1024–65535: {p!r}", f"--port must be 1024-65535: {p!r}"))
    return n


def default_port() -> int:
    """Default port for site init — CERA_SITE_PORT (tests, multiple sites) if set."""
    try:
        return check_port(_sibling("i18n").env("SITE_PORT") or DEFAULT_PORT)
    except SystemExit:
        return DEFAULT_PORT


def port_in_use(port: int) -> bool:
    """Is anyone listening on 127.0.0.1:port? — try connecting (an answer means in use), then try binding like the server does (SO_REUSEADDR)."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", int(port)))
        return False
    except OSError:
        return True
    finally:
        s.close()


def free_port(start: int) -> int:
    """A free port within 100 of start (if none, one chosen by the OS)."""
    for p in range(max(1024, int(start)), min(65536, int(start) + 100)):
        if not port_in_use(p):
            return p
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _idflag(sid) -> str:
    return "" if sid == DEFAULT_ID else f" --id {sid}"


def port_fix_cmd(sid, alt) -> str:
    return f"cera site port {alt}{_idflag(sid)}"


def port_conflict_text(sid, port, alt, token_mode=False) -> str:
    lines = [T(f"포트 {port}: 다른 프로그램이 쓰는 중이라 켜지 못했다.", f"Port {port}: another program is using it, so the board could not start."),
             T(f"  고치기(그대로 붙여 넣기):  {port_fix_cmd(sid, alt)}", f"  Fix (paste as is):  {port_fix_cmd(sid, alt)}"),
             T(f"  그 뒤:  cera site run{_idflag(sid)}   (늘 켜 두기로 돌고 있으면 저절로 다시 뜬다)", f"  Then:  cera site run{_idflag(sid)}   (if the always-on service is running, it comes back up by itself)"),
             T(f"  어떤 프로그램인지 보기:  lsof -nP -iTCP:{port} -sTCP:LISTEN", f"  See which program it is:  lsof -nP -iTCP:{port} -sTCP:LISTEN")]
    if token_mode:
        lines.append(T(f"  원격 관리 터널이면 대시보드의 Public Hostname 서비스도 http://127.0.0.1:{alt} 로 바꾼다", f"  For a remotely managed tunnel, also change the Public Hostname service in the dashboard to http://127.0.0.1:{alt}"))
    return "\n".join(lines)


def cert_path() -> Path:
    env = os.environ.get("TUNNEL_ORIGIN_CERT")
    return Path(env).expanduser() if env else Path.home() / ".cloudflared" / "cert.pem"


def find_cloudflared(st: Optional[dict] = None) -> Optional[str]:
    w = shutil.which("cloudflared")
    if w:
        return str(Path(w).absolute())
    saved = (st or {}).get("cloudflared")
    return saved if saved and os.access(saved, os.X_OK) else None


def site_dir(ctx, sid) -> Path:
    return ctx.SITES / sid


def mode_ok(path: Path) -> bool:
    """Is it unreadable by others (0600, 0400)?"""
    try:
        return (path.stat().st_mode & 0o077) == 0
    except OSError:
        return False


def need_personal(ctx, sid) -> dict:
    st = ctx.read_state(sid)
    if not st or st.get("mode") != "personal":
        raise SystemExit(T(f"개인 사이트 {sid} 가 없다 — 먼저  cera site init --hostname board.<내도메인>  (순서: cera site guide)", f"There is no personal site {sid} - first run  cera site init --hostname board.<your-domain>  (steps: cera site guide)"))
    return st


def creds_tunnel_id(path: Path) -> Optional[str]:
    try:
        tid = json.loads(path.read_text(encoding="utf-8")).get("TunnelID")
    except (OSError, ValueError, AttributeError):
        return None
    return tid if isinstance(tid, str) and UUID_RE.fullmatch(tid) else None


# ------------------------------------------------------------------ generated files
def _y(s) -> str:
    """YAML scalar — a JSON string is a double-quoted string in YAML 1.2 (safe for spaces and colons in paths too)."""
    return json.dumps(str(s), ensure_ascii=False)


def render_config(st: dict) -> str:
    """cloudflared.yml — for this one site only (separate from the global ~/.cloudflared/config.yml)."""
    lines = [
        "# Generated by Cera — `cera site init` rewrites this file (manual edits are lost).",
        f"tunnel: {_y(st.get('tunnel_id') or st['tunnel_name'])}",
        f"credentials-file: {_y(st['credentials_file'])}",
        "ingress:",
        f"  - hostname: {_y(st['hostname'])}",
        f"    service: {_y('http://127.0.0.1:%d' % int(st['port']))}",
    ]
    acc = st.get("access")
    if acc:
        # double verification at the front — cloudflared checks the Access JWT before passing to the origin (the server verifies it separately too)
        lines += ["    originRequest:", "      access:", "        required: true",
                  f"        teamName: {_y(acc['team'])}", "        audTag:", f"          - {_y(acc['aud'])}"]
    lines += ["  - service: http_status:404", ""]
    return "\n".join(lines)


def tunnel_argv(st: dict, cf: str, sites: Path) -> List[str]:
    if st.get("token_file"):
        return [cf, "tunnel", "--no-autoupdate", "run", "--token-file", st["token_file"]]
    # Referring by UUID means cert.pem is not needed at run time (with a name, cloudflared uses the cert to look up the UUID)
    return [cf, "tunnel", "--no-autoupdate", "--config", str(sites / st["id"] / "cloudflared.yml"), "run",
            st.get("tunnel_id") or st["tunnel_name"]]


# Before 0.5.0 the service was named after tempboard. New installs use the cera names; uninstall (and install, which replaces it)
# also finds the old-named service — only a file this program wrote (its label or description is inside) is touched.
OLD_SERVICE_PREFIX = ("com.tempboard.", "tempboard-")


def service_label(sid, legacy=False) -> str:
    return f"{OLD_SERVICE_PREFIX[0] if legacy else 'com.cera.'}{sid}"


def unit_name(sid, legacy=False) -> str:
    return f"{OLD_SERVICE_PREFIX[1] if legacy else 'cera-'}{sid}.service"


def service_description(sid, legacy=False) -> str:
    return f"{'tempboard' if legacy else 'Cera'} personal board ({sid})"


def platform_name(arg=None) -> Optional[str]:
    if arg:
        return arg
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


def service_path(sid, plat, legacy=False) -> Path:
    if plat == "macos":
        return Path.home() / "Library" / "LaunchAgents" / f"{service_label(sid, legacy)}.plist"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user" / unit_name(sid, legacy)


def service_is_ours(path: Path, sid, legacy=False) -> bool:
    """Whether the file at path is the service file this program wrote for sid (its label or description is inside)."""
    try:
        body = path.read_bytes()
    except OSError:
        return False
    return service_label(sid, legacy).encode() in body or service_description(sid, legacy).encode() in body


def installed_service(sid, plat):
    """(path, legacy) of the service file in place — the cera one, else an old tempboard one written by this program; (cera path, False) if neither."""
    path = service_path(sid, plat)
    if not path.exists():
        old = service_path(sid, plat, legacy=True)
        if old.exists() and service_is_ours(old, sid, legacy=True):
            return old, True
    return path, False


def _service_env(ctx, cf: Optional[str]) -> dict:
    dirs = ([str(Path(cf).parent)] if cf else []) + SYS_PATH
    env = {"PATH": ":".join(dict.fromkeys(dirs)), "CERA_HOME": str(Path(ctx.HOME).expanduser().absolute()),
           "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "LANG": "en_US.UTF-8",
           "CERA_SERVICE": "1"}   # lets site run know it started 'as a service' — if a supervisor is already running, it waits and takes over
    dist = _sibling("i18n").env("DIST")
    if dist:
        env["CERA_DIST"] = str(Path(dist).expanduser().absolute())
    return env


def _installer(ctx):
    return ctx.load_module("install_agents", HERE / "install_agents.py")


def program_args(ctx, sid) -> List[str]:
    """Command the service will run. The plugin root (<ROOT>/bin/cera) can disappear when the plugin is upgraded, so this points to the fixed entry point
    $CERA_HOME/bin/cera (used by site install — install_agents.write_launcher)."""
    py = sys.executable
    if not py or not os.path.isabs(py):
        raise SystemExit(T("파이썬 절대경로를 알 수 없다(sys.executable) — 절대경로의 python3 로 다시 실행하세요", "Cannot determine the absolute path of Python (sys.executable) - run again with python3 by its absolute path"))
    return [py, str(_installer(ctx).launcher_path(ctx.HOME)), "site", "run", "--id", sid]


def render_plist(ctx, st: dict, cf: Optional[str]) -> bytes:
    """LaunchAgent. No secrets (keys, tokens) go in — run reads only the state.json and token file paths."""
    sid = st["id"]
    log = str(site_dir(ctx, sid).absolute() / "log.txt")
    d = {
        "Label": service_label(sid),
        "ProgramArguments": program_args(ctx, sid),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},   # if it ends cleanly (0) via `cera stop`, do not start it again
        "ThrottleInterval": 10,
        "WorkingDirectory": str(site_dir(ctx, sid).absolute()),
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": _service_env(ctx, cf),
    }
    return plistlib.dumps(d, sort_keys=True)


def _sq(s: str) -> str:
    """A double-quoted systemd word — escape \\ and " and turn % into %% (to prevent specifier expansion)."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def render_systemd(ctx, st: dict, cf: Optional[str]) -> str:
    sid = st["id"]
    log = str(site_dir(ctx, sid).absolute() / "log.txt").replace("%", "%%")
    env = "\n".join(f"Environment={_sq(k + '=' + v)}" for k, v in _service_env(ctx, cf).items())
    return f"""\
# Generated by Cera — `cera site install` rewrites this file.
[Unit]
Description={service_description(sid)}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={' '.join(_sq(x) for x in program_args(ctx, sid))}
Restart=on-failure
RestartSec=5
{env}
StandardOutput=append:{log}
StandardError=append:{log}

[Install]
WantedBy=default.target
"""


def _write_atomic(path: Path, data: bytes, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# ------------------------------------------------------------------ calling cloudflared
class StepError(Exception):
    pass


def _cf(argv: List[str], step: str, timeout=120) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise StepError(f"{step}: {type(e).__name__}: {e}")
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip()[-1200:]
        raise StepError(T(f"{step} 실패(exit {p.returncode}):\n{tail}", f"{step} failed (exit {p.returncode}):\n{tail}"))
    return p.stdout


class RefuseError(StepError):
    """A case where a human must decide — exit 2."""


def default_tunnel_name(host: str) -> str:
    """Put the public address into the default tunnel name — so it does not collide with other machines or other sites on the same account."""
    return ("cera-" + host.replace(".", "-"))[:63].rstrip("-.")


def _existing_tunnel(cf, cert, name) -> Optional[dict]:
    out = _cf([cf, "tunnel", "--origincert", str(cert), "list", "--output", "json", "--name", name], "tunnel list")
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        raise StepError(T("tunnel list 출력을 읽지 못했다:\n", "Could not read the output of tunnel list:\n") + out[-500:])
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("name") == name:   # list hides deleted tunnels by default
            conns = r.get("connections")
            return {"id": str(r.get("id") or "") or name, "connections": len(conns) if isinstance(conns, list) else None}
    return None


# ------------------------------------------------------------------ init
def cmd_init(ctx, a):
    sid = check_id(a.id)
    host = check_hostname(a.hostname)
    port = check_port(a.port) if a.port is not None else default_port()
    cfm = _cf_mod(ctx)
    access_new = None
    if a.no_access and (a.access_team or a.access_aud):
        raise SystemExit(T("--no-access 와 --access-team/--access-aud 는 함께 쓸 수 없다", "--no-access cannot be combined with --access-team/--access-aud"))
    if a.access_team or a.access_aud:
        if not (a.access_team and a.access_aud):
            raise SystemExit(T("--access-team 과 --access-aud 는 함께 준다(순서: cera site guide 의 A–E)", "Give --access-team and --access-aud together (steps A-E of cera site guide)"))
        try:
            access_new = {"team": cfm.team_name(a.access_team), "aud": cfm.check_aud(a.access_aud)}
        except ValueError as e:
            raise SystemExit(str(e))
    token_file = None
    if a.token_file:
        tf = Path(a.token_file).expanduser().absolute()
        if not tf.is_file() or tf.stat().st_size == 0:
            print(T(f"토큰 파일이 없거나 비었다: {tf}\n\n", f"The token file is missing or empty: {tf}\n\n") + GUIDE_TOKEN.format(port=port), file=sys.stderr)
            sys.exit(2)
        if not mode_ok(tf):
            print(T(f"토큰 파일을 남도 읽을 수 있다 — 먼저  chmod 600 {tf}", f"Other users can read the token file - first run  chmod 600 {tf}"), file=sys.stderr)
            sys.exit(2)
        token_file = str(tf)

    cf = find_cloudflared()
    if not cf:
        print(T("cloudflared 가 없다 — macOS: brew install cloudflared · 그 밖: "
              "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n\n", "cloudflared is not installed - macOS: brew install cloudflared - elsewhere: "
              "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n\n") + guide_text(ctx, sid, port),
              file=sys.stderr)
        sys.exit(2)
    cert = cert_path()
    if not token_file and not cert.is_file():
        print(T(f"Cloudflare 로그인 파일이 없다: {cert}\n아직 아무것도 만들지 않았다.\n\n", f"The Cloudflare login file is missing: {cert}\nNothing has been created yet.\n\n") + GUIDE_DOMAIN.format(stop=stop_howto(sid)) + "\n" + GUIDE_TOKEN.format(port=port),
              file=sys.stderr)
        sys.exit(2)

    old = ctx.read_state(sid)
    if old and old.get("mode") != "personal":
        raise SystemExit(T(f"{sid} 는 임시 사이트 id 다 — 다른 --id 를 쓰세요", f"{sid} is the id of a temporary site - use a different --id"))
    old = old or {}
    # port — if not given, use the previous port (or the default); if another program is using that port, pick a free one and say so
    if a.port is None and old.get("port"):
        port = int(old["port"])
    ours = old.get("status") in ("running", "starting") and ctx.alive(old.get("pid")) and int(old.get("port") or 0) == port
    port_note = None
    if not ours and port_in_use(port):
        alt = free_port(port + 1)
        if a.port is None:
            port_note = T(f"포트 {port}: 다른 프로그램이 쓰는 중 → 이 사이트는 포트 {alt} 사용(바꾸려면 cera site port N{_idflag(sid)})", f"Port {port}: another program is using it -> this site uses port {alt} (to change it: cera site port N{_idflag(sid)})")
            port = alt
        else:
            print(T(f"주의: 포트 {port}: 다른 프로그램이 쓰는 중 — 이대로면 site run 이 켜지지 않는다. "
                  f"비어 있는 포트: {alt}  (다시: --port {alt}, 또는 나중에 {port_fix_cmd(sid, alt)})", f"Warning: port {port}: another program is using it - as it stands, site run will not start. "
                  f"Free port: {alt}  (run again with --port {alt}, or later {port_fix_cmd(sid, alt)})"), file=sys.stderr)
    d = site_dir(ctx, sid)
    created = not d.exists()
    (d / "data").mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    name = a.tunnel_name or old.get("tunnel_name") or default_tunnel_name(host)
    if not TUNNEL_NAME_RE.match(name):
        raise SystemExit(T(f"--tunnel-name 을 읽지 못했다: {name!r}", f"Could not read --tunnel-name: {name!r}"))
    creds = d / "tunnel.json"
    tunnel_id = None
    try:
        if not token_file:
            if creds.is_file() and old.get("tunnel_name") == name:
                tunnel_id = creds_tunnel_id(creds)
            if not tunnel_id:
                if creds.is_file():   # credentials of another tunnel — set aside rather than deleted
                    creds.rename(creds.with_name(f"tunnel.json.bak-{int(time.time())}"))
                found = _existing_tunnel(cf, cert, name)
                if found and not a.reuse_tunnel:
                    raise RefuseError(
                        T(f"Cloudflare 계정에 터널 {name} 이 이미 있다(id {found['id']}). 이 컴퓨터가 몰래 이어받지 않는다:\n"
                        "  두 컴퓨터가 한 터널로 돌면 Cloudflare 가 요청을 둘에 나눠 보내 그림이 갈리고 로그인이 번갈아 끊긴다.\n"
                        "  → 다른 컴퓨터에서 쓰던 보드를 이 컴퓨터로 옮기는 것이면: 그쪽을 끄고(cera site uninstall --load ·\n"
                        "    cera stop) 다시  cera site init … --reuse-tunnel\n"
                        "  → 따로 쓰려면 다른 이름:  --tunnel-name <새 이름>  (주소도 다르게 --hostname)", f"A tunnel named {name} already exists in your Cloudflare account (id {found['id']}). This computer will not quietly take it over:\n"
                        "  If two computers run one tunnel, Cloudflare splits requests between them, so drawings diverge and logins drop in turn.\n"
                        "  -> If you are moving a board from another computer to this one: turn it off there (cera site uninstall --load and\n"
                        "    cera stop), then run  cera site init ... --reuse-tunnel  again\n"
                        "  -> To use them separately, pick another name:  --tunnel-name <new name>  (and a different --hostname)"))
                if found:
                    n = found.get("connections")
                    print(T(f"터널 {name} 을 이어받는다(--reuse-tunnel) — 자격 파일만 받는다", f"Taking over tunnel {name} (--reuse-tunnel) - only fetching the credentials file"), file=sys.stderr)
                    if n:
                        print(T(f"주의: 이 터널에 지금 연결 {n}개가 붙어 있다 — 다른 컴퓨터에서 돌고 있으면 그쪽을 먼저 끄세요", f"Warning: {n} connection(s) are attached to this tunnel right now - if it is running on another computer, turn that one off first"), file=sys.stderr)
                    _cf([cf, "tunnel", "--origincert", str(cert), "token", "--cred-file", str(creds), name], "tunnel token")
                else:
                    out = _cf([cf, "tunnel", "--origincert", str(cert), "create", "--credentials-file", str(creds), name], "tunnel create")
                    print(out.strip(), file=sys.stderr)
                if not creds.is_file():
                    raise StepError(T(f"자격 파일이 생기지 않았다: {creds}", f"The credentials file was not created: {creds}"))
                os.chmod(creds, 0o600)
                tunnel_id = creds_tunnel_id(creds)
            route = [cf, "tunnel", "--origincert", str(cert), "route", "dns"] + (["--overwrite-dns"] if a.overwrite_dns else []) + [name, host]
            try:
                _cf(route, "tunnel route dns")
            except StepError as e:
                raise StepError(T(f"{e}\n→ 이미 {host} 에 다른 DNS 레코드가 있으면: 대시보드에서 지우거나, 덮어써도 되면 --overwrite-dns", f"{e}\n-> If {host} already has another DNS record: delete it in the dashboard, or use --overwrite-dns if overwriting is fine"))
    except StepError as e:
        if created and not creds.exists():
            shutil.rmtree(d, ignore_errors=True)   # only the empty folder created this time — if a credentials file was created, keep it (the next init will overwrite it)
        if isinstance(e, RefuseError):
            print(str(e), file=sys.stderr)
            sys.exit(2)
        raise SystemExit(T(f"site init 을 마치지 못했다 — {e}", f"site init did not finish - {e}"))

    new_keys = []

    def fill(cur):
        if cur.get("mode") not in (None, "personal"):
            raise SystemExit(T(f"{sid} 는 임시 사이트 id 다", f"{sid} is the id of a temporary site"))
        if not cur.get("edit_key") or (a.view_link and not cur.get("view_key")):
            new_keys.append(True)
        acc = cur.get("access")
        if a.no_access:
            acc = None
        elif access_new:
            acc = access_new
        cur.update({
            "id": sid, "mode": "personal", "title": a.title or cur.get("title") or T("개인 보드", "Personal board"), "board": cur.get("board"),
            "created_at": cur.get("created_at") or time.time(), "expires_at": 0,
            "edit_key": cur.get("edit_key") or secrets.token_urlsafe(18),
            "view_key": cur.get("view_key") or (secrets.token_urlsafe(18) if a.view_link else None),
            "tunnel": "named", "tunnel_name": None if token_file else name, "tunnel_id": tunnel_id,
            "credentials_file": None if token_file else str(creds), "token_file": token_file,
            "hostname": host, "public_url": f"https://{host}", "host": "127.0.0.1", "port": port,
            "access": acc, "keep_awake": cur.get("keep_awake", False) if a.keep_awake is None else a.keep_awake,
            "cloudflared": cf, "status": cur.get("status") or "stopped", "pid": cur.get("pid"),
            "local_url": cur.get("local_url") if int(cur.get("port") or 0) == port else None, "error": None,
        })
    st = ctx.update_state(sid, fill)

    cfg = d / "cloudflared.yml"
    if token_file:
        if cfg.exists():
            cfg.rename(cfg.with_name(f"cloudflared.yml.bak-{int(time.time())}"))
    else:
        _write_atomic(cfg, render_config(st).encode("utf-8"), 0o600)
        try:
            _cf([cf, "tunnel", "--config", str(cfg), "ingress", "validate"], "ingress validate", timeout=30)
            rule = _cf([cf, "tunnel", "--config", str(cfg), "ingress", "rule", f"https://{host}/"], "ingress rule", timeout=30)
        except StepError as e:
            raise SystemExit(T(f"설정 파일 점검 실패 — {e}\n파일: {cfg}", f"Config file check failed - {e}\nFile: {cfg}"))
        if "#0" not in rule:
            raise SystemExit(T(f"설정 파일 점검: https://{host}/ 이 첫 규칙에 맞지 않는다\n{rule}", f"Config file check: https://{host}/ does not match the first rule\n{rule}"))

    print(T(f"개인 사이트 {sid} 준비 — https://{host}/ · 이 컴퓨터 포트 {port}", f"Personal site {sid} is ready - https://{host}/ - port {port} on this computer"))
    if port_note:
        print("  " + port_note)
    if token_file:
        print(T(f"  원격 관리 터널(토큰 파일 {token_file}) — 대시보드의 Public Hostname 이 {host} → http://127.0.0.1:{port} 이어야 한다", f"  Remotely managed tunnel (token file {token_file}) - the Public Hostname in the dashboard must be {host} -> http://127.0.0.1:{port}"))
    else:
        print(T(f"  터널 {name} ({tunnel_id or '?'}) · 설정 {cfg}", f"  Tunnel {name} ({tunnel_id or '?'}) - config {cfg}"))
    if st.get("access"):
        print(T(f"  Cloudflare Access: 팀 {st['access']['team']} — 공개 주소는 Access 로그인 뒤 열린다(초대 키 필요 없음)", f"  Cloudflare Access: team {st['access']['team']} - the public address opens after the Access login (no invite key needed)"))
        print(T(f"  편집 · 보기 나누기(선택): {d / 'members.json'}  (형식: cera site guide 의 F)", f"  Separate editors and viewers (optional): {d / 'members.json'}  (format: step F of cera site guide)"))
    else:
        if new_keys:   # shown only this one time, when the keys are first created (an exception to the secrets rule — never printed in the log or site run)
            ln = ctx.links({**st, "local_url": f"http://127.0.0.1:{port}"})
            print(T("  초대 링크(지금 한 번만 보인다 — 다시 보기: cera links " + sid + "):", "  Invite links (shown only this once - to see them again: cera links " + sid + "):"))
            print(T(f"    같이 그리기   {ln['edit']}", f"    Draw together {ln['edit']}"))
            if ln.get("view"):
                print(T(f"    보기 전용     {ln['view']}", f"    View only     {ln['view']}"))
            if ln.get("local"):
                print(T(f"    (이 컴퓨터)   {ln['local']}   ← site run 을 켠 뒤", f"    (this computer) {ln['local']}   <- after starting site run"))
        else:
            print(T(f"  초대 링크:  cera links {sid}", f"  Invite links:  cera links {sid}"))
        print(T("  Access 없이 초대 키로 연다 — 링크가 퍼지면  cera keys " + sid + " --rotate all", "  It opens with the invite key, without Access - if the link spreads:  cera keys " + sid + " --rotate all"))
    if old.get("status") == "running" and ctx.alive(old.get("pid")):
        print(T("  ※ 지금 돌고 있다 — 터널 · 포트 · Access 를 바꿨으면 다시 켜야 먹는다"
              f"(전경: Ctrl+C 뒤 site run · 서비스: cera site install --load)", "  Note: it is running right now - if you changed the tunnel, port or Access, restart it for the change to apply "
              f"(foreground: Ctrl+C, then site run - service: cera site install --load)"))
    if st.get("access"):
        err = cfm.Members(d / "members.json").check()
        if err:
            print(T(f"주의: members.json 이 틀려 지금은 아무도 못 들어온다 — {err}", f"Warning: members.json is malformed, so nobody can get in right now - {err}"), file=sys.stderr)
    print(T("다음:  cera site run   →   cera site doctor   →   cera site install --load", "Next:  cera site run   ->   cera site doctor   ->   cera site install --load"))


# ------------------------------------------------------------------ run
MISSING_CF = (T("import sys,time; sys.stderr.write('cloudflared 를 찾지 못했다 — brew install cloudflared 뒤 기다리면 다시 연다\\n'); "
              "time.sleep(1); sys.exit(127)", "import sys,time; sys.stderr.write('cloudflared not found - it will reopen once brew install cloudflared has finished\\n'); "
              "time.sleep(1); sys.exit(127)"))


def make_access(ctx, sid, st):
    acc = st.get("access")
    if not acc:
        return None
    cfm = _cf_mod(ctx)
    cfm.refuse_with_trust_header(st.get("trust_header"))
    try:
        ca = cfm.CfAccess(acc["team"], acc["aud"], jwks_url=acc.get("jwks_url"), members_path=site_dir(ctx, sid) / "members.json")
    except (ValueError, KeyError, TypeError) as e:
        raise SystemExit(T(f"Access 설정을 읽지 못했다 — {e}", f"Could not read the Access settings - {e}"))
    err = ca.members.check()
    if err:   # fail closed, but leave a prominent note at the top of the log (doctor also reports FAIL)
        print(T(f"주의: members.json 이 틀려 공개 주소로는 아무도 못 들어온다 — {err}", f"Warning: members.json is malformed, so nobody can get in through the public address - {err}"), file=sys.stderr, flush=True)
    return ca


def _other_running(ctx, st) -> bool:
    return st.get("status") in ("running", "starting") and ctx.alive(st.get("pid")) and st.get("pid") != os.getpid()


def _wait_for_other(ctx, sid, st, poll=2.0):
    """Started as a service (launchd, systemd) while another site run (foreground) is already running — if it exited with 1, it would restart every 10 seconds and fill the log.
    Instead, wait quietly and take over when that one ends. If a human stopped it via `cera stop` (stop_requested_at), it ends with exit 0
    and does not restart (KeepAlive{SuccessfulExit:false})."""
    t0 = time.time()
    print(T(f"다른 site run(pid {st.get('pid')})이 돌고 있다 — 끝나면 이어받는다", f"Another site run (pid {st.get('pid')}) is running - taking over when it ends"), flush=True)
    while True:
        time.sleep(poll)
        cur = ctx.read_state(sid) or {}
        if (cur.get("stop_requested_at") or 0) >= t0:
            print(T("cera stop 으로 꺼졌다 — 다시 띄우지 않는다", "It was stopped with cera stop - not starting it again"), flush=True)
            sys.exit(0)
        if not _other_running(ctx, cur):
            return need_personal(ctx, sid)


def cmd_run(ctx, a):
    started = time.time()
    sid = check_id(a.id)
    st = need_personal(ctx, sid)
    if _other_running(ctx, st):
        if _sibling("i18n").env("SERVICE") != "1":
            raise SystemExit(T(f"{sid} 는 이미 돌고 있다(pid {st['pid']}) — 끝내려면 cera stop {sid}", f"{sid} is already running (pid {st['pid']}) - to stop it: cera stop {sid}"))
        st = _wait_for_other(ctx, sid, st)
    if st.get("trust_header"):
        raise SystemExit(T("개인 사이트에는 trust_header 를 쓸 수 없다(위조된다) — state.json 에서 지우세요", "trust_header cannot be used on a personal site (it can be forged) - remove it from state.json"))
    ca = make_access(ctx, sid, st)
    ctx.ensure_dist()
    sup = ctx.supervise()

    def tunnel_cmd(port):
        try:   # when the tunnel is (re)opened — doctor uses this to flag a 'tunnel that keeps dying'
            ctx.update_state(sid, lambda c: c.update(tunnel_starts=((c.get("tunnel_starts") or []) + [time.time()])[-20:]))
        except Exception:   # noqa: BLE001 — a failed write must never prevent opening the tunnel
            pass
        cf = find_cloudflared(st)
        if not cf:   # so the tunnel thread does not die — back off with a placeholder process that fails
            return [sys.executable, "-c", MISSING_CF]
        return tunnel_argv(st, cf, ctx.SITES)

    def on_start(port, stop):
        if ca is not None:
            ca.warm()
        if not st.get("keep_awake"):
            return None
        caf = shutil.which("caffeinate") if sys.platform == "darwin" else None
        if not caf:
            print(T("주의: --keep-awake 는 macOS 의 caffeinate 로만 — 잠자기 설정은 시스템에서", "Warning: --keep-awake works only through macOS caffeinate - change sleep settings in the system settings"), file=sys.stderr, flush=True)
            return None
        # -s: prevent sleep while on AC power · -w: ends together with this process if it dies (even via SIGKILL)
        p = subprocess.Popen([caf, "-s", "-w", str(os.getpid())], stdin=subprocess.DEVNULL)

        def cleanup():
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(3)
                except subprocess.TimeoutExpired:
                    p.kill()
        return cleanup

    # If `cera stop` arrived while fetching the web files or building (a few minutes), do not start the supervisor (the service gets exit 0 -> not restarted)
    if ((ctx.read_state(sid) or {}).get("stop_requested_at") or 0) >= started:
        print(T("시작하는 동안 cera stop 이 왔다 — 띄우지 않는다", "cera stop arrived during startup - not starting"), flush=True)
        sys.exit(0)
    port = int(st.get("port") or DEFAULT_PORT)
    if port_in_use(port):   # another program holds the port — say which port and give a fix command to paste
        print(port_conflict_text(sid, port, free_port(port + 1), bool(st.get("token_file"))), file=sys.stderr, flush=True)
        ctx.update_state(sid, lambda c: c.update(error=f"port-in-use:{port}"))
        sys.exit(1)
    ctx.update_state(sid, lambda c: c.update(tunnel_starts=[], error=None))
    # Do not print the keys — this line goes into the always-on log.txt
    how = T("Cloudflare Access 로그인으로 연다", "it opens through the Cloudflare Access login") if st.get("access") else T(f"초대 링크: cera links {sid}", f"invite links: cera links {sid}")
    print(f"Cera site {sid} — https://{st['hostname']}/ · {how}", flush=True)
    print(stop_howto(sid), flush=True)
    try:
        reason = sup.run(sid, st, tunnel_cmd, None, retries=None, backoff=sup.exp_backoff, sites=ctx.SITES, dist=ctx.DIST,
                         server=ctx.load_server(), authn=[ca] if ca else [], hsts=True, on_start=on_start, echo_tunnel=True)
    except SystemExit:
        raise
    except OSError as e:
        if e.errno == errno.EADDRINUSE:   # someone grabbed it between the check and the start
            print(port_conflict_text(sid, port, free_port(port + 1), bool(st.get("token_file"))), file=sys.stderr, flush=True)
            ctx.update_state(sid, lambda c: c.update(error=f"port-in-use:{port}"))
        else:
            print(T(f"site run 실패: {type(e).__name__}: {e}", f"site run failed: {type(e).__name__}: {e}"), file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as e:   # noqa: BLE001 — exit with a failure code so launchd restarts it
        print(T(f"site run 실패: {type(e).__name__}: {e}", f"site run failed: {type(e).__name__}: {e}"), file=sys.stderr, flush=True)
        sys.exit(1)
    sys.exit(0 if reason in ("stopped", "expired") else 1)


# ------------------------------------------------------------------ port
def cmd_port(ctx, a):
    """Change only the port — tunnel, DNS, and keys stay (not blocked by the tunnel refusal). If locally managed, rewrite cloudflared.yml."""
    sid = check_id(a.id)
    st = need_personal(ctx, sid)
    if _other_running(ctx, st):
        raise SystemExit(T(f"{sid} 가 돌고 있다 — 포트를 바꾸려면 먼저 cera stop {sid}", f"{sid} is running - to change the port, first run cera stop {sid}"))
    old = int(st.get("port") or DEFAULT_PORT)
    if a.port in (None, "auto"):
        new = old if not port_in_use(old) else free_port(old + 1)
    else:
        new = check_port(a.port)
        if port_in_use(new):
            raise SystemExit(T(f"포트 {new}: 이것도 다른 프로그램이 쓰는 중 — 비어 있는 포트: {free_port(new + 1)}", f"Port {new}: another program is using this one too - free port: {free_port(new + 1)}"))
    st = ctx.update_state(sid, lambda c: c.update(port=new, local_url=None, error=None))
    if not st.get("token_file"):
        cfg = site_dir(ctx, sid) / "cloudflared.yml"
        _write_atomic(cfg, render_config(st).encode("utf-8"), 0o600)
        cf = find_cloudflared(st)
        if cf:
            try:
                _cf([cf, "tunnel", "--config", str(cfg), "ingress", "validate"], "ingress validate", timeout=30)
            except StepError:
                raise SystemExit(T(f"설정 파일 점검 실패 — cera site doctor --verbose{_idflag(sid)} 로 원문을 보세요", f"Config file check failed - see the raw text with cera site doctor --verbose{_idflag(sid)}"))
    print(T(f"포트: {old} → {new}", f"Port: {old} -> {new}") if new != old else T(f"포트 {new}: 비어 있음 — 그대로 쓴다", f"Port {new}: free - keeping it"))
    if st.get("token_file"):
        print(T(f"  원격 관리 터널 — 대시보드의 Public Hostname 서비스를 http://127.0.0.1:{new} 로 바꾸세요", f"  Remotely managed tunnel - change the Public Hostname service in the dashboard to http://127.0.0.1:{new}"))
    print(T(f"다음:  cera site run{_idflag(sid)}   (늘 켜 두기로 돌고 있으면 10초 안에 저절로 다시 뜬다)", f"Next:  cera site run{_idflag(sid)}   (if the always-on service is running, it comes back up by itself within 10 seconds)"))


# ------------------------------------------------------------------ install · uninstall
def _uid() -> int:
    return os.getuid()


def load_cmds(sid, plat, path: Path) -> List[List[str]]:
    if plat == "macos":
        return [["launchctl", "bootout", f"gui/{_uid()}/{service_label(sid)}"], ["launchctl", "bootstrap", f"gui/{_uid()}", str(path)]]
    unit = path.name
    return [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", unit]]


def unload_cmds(sid, plat, path: Path, legacy=False) -> List[List[str]]:
    if plat == "macos":
        return [["launchctl", "bootout", f"gui/{_uid()}/{service_label(sid, legacy)}"]]
    return [["systemctl", "--user", "disable", "--now", path.name], ["systemctl", "--user", "daemon-reload"]]


def remove_legacy_service(sid, plat, load: bool, dry_run=False) -> None:
    """Remove the old tempboard-named service for sid (com.tempboard.<id> / tempboard-<id>.service), but only a file this
    program wrote. With load, it is unloaded first; otherwise the commands to unload it are printed."""
    path = service_path(sid, plat, legacy=True)
    if not path.exists():
        return
    cmds = unload_cmds(sid, plat, path, legacy=True)
    if not service_is_ours(path, sid, legacy=True):
        print(T(f"옛 이름 자리에 파일이 있지만 이 프로그램이 만든 것이 아니다 — 손대지 않았다: {path}",
                f"A file sits at the old service name but was not made by this program - left untouched: {path}"))
        return
    if dry_run:
        print(T(f"# 옛 이름(tempboard)의 서비스도 지운다: {path}\n# 내리기(--load):\n{_show_cmds(cmds)}",
                f"# The old-named (tempboard) service is removed too: {path}\n# To unload (--load):\n{_show_cmds(cmds)}"))
        return
    if load:
        for c in cmds:
            p = subprocess.run(c, capture_output=True, text=True)
            if p.returncode != 0:
                print(T(f"주의: {' '.join(c)} → exit {p.returncode} {(p.stderr or p.stdout).strip()}", f"Warning: {' '.join(c)} -> exit {p.returncode} {(p.stderr or p.stdout).strip()}"), file=sys.stderr)
    path.unlink()
    print(T(f"옛 이름(tempboard)의 서비스 파일을 지웠다: {path}", f"Deleted the old-named (tempboard) service file: {path}"))
    if not load:
        print(T(f"※ 그 서비스가 올라가 있었다면 아직 돌고 있다 — 내리려면:\n{_show_cmds(cmds)}",
                f"Note: if that service was loaded, it is still running - to unload it:\n{_show_cmds(cmds)}"))


def _show_cmds(cmds):
    return "\n".join("  " + " ".join(c) for c in cmds)


def cmd_install(ctx, a):
    sid = check_id(a.id)
    st = need_personal(ctx, sid)
    plat = platform_name(a.platform)
    if plat not in ("macos", "linux"):
        raise SystemExit(T("늘 켜 두기는 macOS(launchd) · Linux(systemd) 만", "The always-on service works only on macOS (launchd) and Linux (systemd)"))
    cf = find_cloudflared(st)
    path = service_path(sid, plat)
    data = render_plist(ctx, st, cf) if plat == "macos" else render_systemd(ctx, st, cf).encode("utf-8")
    cmds = load_cmds(sid, plat, path)
    if a.dry_run:
        print(T(f"# {path}  (쓰지 않았다 — --dry-run)", f"# {path}  (not written - --dry-run)"))
        sys.stdout.write(data.decode("utf-8"))
        print(T(f"# 올리기(--load):\n{_show_cmds(cmds)}", f"# To load (--load):\n{_show_cmds(cmds)}"))
        remove_legacy_service(sid, plat, a.load, dry_run=True)
        return
    if not cf:
        print(T("주의: cloudflared 를 찾지 못했다 — 설치한 뒤 이 명령을 다시 실행하세요(서비스 PATH 에 넣는다)", "Warning: cloudflared was not found - install it, then run this command again (it is added to the service PATH)"), file=sys.stderr)
    remove_legacy_service(sid, plat, a.load)   # the same site under the old name would otherwise run twice
    launcher = _installer(ctx).write_launcher(ctx.HOME, ctx.ROOT)
    print(T(f"고정 진입점: {launcher} → {ctx.ROOT}(플러그인을 올린 뒤에는 이 명령을 다시 실행하면 새 판을 가리킨다)", f"Fixed entry point: {launcher} -> {ctx.ROOT} (after updating the plugin, run this command again so it points to the new version)"))
    _write_atomic(path, data, 0o644)
    ctx.update_state(sid, lambda cur: cur.update(service={"platform": plat, "path": str(path)}))
    print(T(f"썼다: {path}", f"Wrote: {path}"))
    if not a.load:
        print(T(f"아직 올리지 않았다 — 올리려면  cera site install --load   (또는 직접:\n{_show_cmds(cmds)})", f"Not loaded yet - to load it run  cera site install --load   (or by hand:\n{_show_cmds(cmds)})"))
    else:
        for i, c in enumerate(cmds):
            p = subprocess.run(c, capture_output=True, text=True)
            if p.returncode != 0 and not (plat == "macos" and i == 0):   # the first bootout fails if it was not loaded — that is fine
                raise SystemExit(T(f"{' '.join(c)} 실패(exit {p.returncode}): {(p.stderr or p.stdout).strip()}", f"{' '.join(c)} failed (exit {p.returncode}): {(p.stderr or p.stdout).strip()}"))
        print(T("올렸다 — 로그인할 때마다 켜지고, 죽으면 다시 띄운다. 끄기:  cera site uninstall --load", "Loaded - it starts at every login and restarts if it dies. To remove it:  cera site uninstall --load"))
    if plat == "linux":
        print(T("※ 로그아웃 뒤에도 돌게 하려면 한 번:  loginctl enable-linger $USER", "Note: to keep it running after you log out, run once:  loginctl enable-linger $USER"))
    if plat == "macos":
        print(T("※ 노트북은 잠들면 주소가 꺼진다 — 전원 연결 때 잠자기를 막으려면 site init --keep-awake, 늘 켜 둘 기계(Mac mini)가 가장 좋다", "Note: a laptop's address goes down when it sleeps - to prevent sleep while on AC power use site init --keep-awake; a machine that stays on (such as a Mac mini) is best"))


def cmd_uninstall(ctx, a):
    sid = check_id(a.id)
    plat = platform_name(a.platform)
    if plat not in ("macos", "linux"):
        raise SystemExit(T("늘 켜 두기는 macOS(launchd) · Linux(systemd) 만", "The always-on service works only on macOS (launchd) and Linux (systemd)"))
    path = service_path(sid, plat)
    cmds = unload_cmds(sid, plat, path)
    if a.dry_run:
        print(T(f"# 지울 파일: {path}{'' if path.exists() else ' (없음)'}\n# 내리기(--load):\n{_show_cmds(cmds)}", f"# File to delete: {path}{'' if path.exists() else ' (missing)'}\n# To unload (--load):\n{_show_cmds(cmds)}"))
        remove_legacy_service(sid, plat, a.load, dry_run=True)
        return
    legacy_only = not path.exists() and service_path(sid, plat, legacy=True).exists()
    remove_legacy_service(sid, plat, a.load)
    if a.load and not legacy_only:   # only the old-named service was installed: it was unloaded above
        for c in cmds:
            p = subprocess.run(c, capture_output=True, text=True)
            if p.returncode != 0:
                print(T(f"주의: {' '.join(c)} → exit {p.returncode} {(p.stderr or p.stdout).strip()}", f"Warning: {' '.join(c)} -> exit {p.returncode} {(p.stderr or p.stdout).strip()}"), file=sys.stderr)
    if path.exists():
        if not service_is_ours(path, sid):
            raise SystemExit(T(f"{path} 는 Cera 가 만든 파일이 아닌 것 같다 — 손대지 않았다", f"{path} does not look like a file made by Cera - left untouched"))
        path.unlink()
        print(T(f"지웠다: {path}", f"Deleted: {path}"))
    elif not legacy_only:
        print(T(f"서비스 파일이 없다: {path}", f"No service file: {path}"))
    if ctx.read_state(sid):
        ctx.update_state(sid, lambda cur: cur.update(service=None))
    if not a.load and not legacy_only:
        print(T(f"※ 이미 올라가 있었다면 지금 도는 것은 그대로다 — 내리려면:\n{_show_cmds(cmds)}", f"Note: if it was already loaded, what is running now keeps running - to unload it:\n{_show_cmds(cmds)}"))
    print(T(f"그림 · 설정({site_dir(ctx, sid)})과 Cloudflare 의 터널 · DNS 는 남는다(지우려면 사람이 대시보드 또는 cloudflared tunnel delete).", f"Drawings and settings ({site_dir(ctx, sid)}) and the tunnel and DNS record on Cloudflare stay (to remove them, a person must use the dashboard or cloudflared tunnel delete)."))


# ------------------------------------------------------------------ status · links
def service_loaded(sid, plat, legacy=False) -> Optional[bool]:
    """Is it loaded? (read-only). None if unknown."""
    try:
        if plat == "macos":
            p = subprocess.run(["launchctl", "print", f"gui/{_uid()}/{service_label(sid, legacy)}"], capture_output=True, text=True, timeout=5)
            return p.returncode == 0
        if plat == "linux":
            p = subprocess.run(["systemctl", "--user", "is-active", unit_name(sid, legacy)], capture_output=True, text=True, timeout=5)
            return p.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def personal_links(ctx, st) -> dict:
    """Links in Access mode (the public address without a key, only this machine's address with the invite key) — links() in bin/cera follows the same rule."""
    return ctx.links(st)


def show_personal(ctx, st, as_json=False):
    """The same output as `cera links` (show() in bin/cera knows about Access mode)."""
    return ctx.show(st, as_json)


def cmd_links(ctx, a):
    st = need_personal(ctx, check_id(a.id))
    if ctx.live_status(st) != "running" and not a.json:
        print(T(f"{st['id']} 는 닫혀 있다({ctx.live_status(st)}) — cera site run 또는 site install --load", f"{st['id']} is closed ({ctx.live_status(st)}) - run cera site run or site install --load"))
        return
    show_personal(ctx, st, a.json)


def human_error(sid, err) -> str:
    """state.error -> human-readable text. For the raw text the supervisor left (OSError etc.), translate only what is recognizable."""
    e = str(err or "")
    m = re.match(r"port-in-use:(\d+)$", e)
    if m or "Address already in use" in e:
        port = int(m.group(1)) if m else None
        return T(f"포트 {port or '?'}: 다른 프로그램이 쓰는 중 — {port_fix_cmd(sid, free_port((port or DEFAULT_PORT) + 1))}", f"Port {port or '?'}: another program is using it - {port_fix_cmd(sid, free_port((port or DEFAULT_PORT) + 1))}")
    return e


def cmd_status(ctx, a):
    sid = check_id(a.id)
    st = need_personal(ctx, sid)
    plat = platform_name()
    sp, legacy = installed_service(sid, plat) if plat else (None, False)
    loaded = service_loaded(sid, plat, legacy) if sp and sp.exists() else None
    members = site_dir(ctx, sid) / "members.json"
    info = {"id": sid, "status": ctx.live_status(st), "pid": st.get("pid") if ctx.alive(st.get("pid")) else None,
            "url": st.get("public_url"), "local_url": st.get("local_url"), "port": st.get("port"),
            "tunnel": {"managed": "remote" if st.get("token_file") else "local", "name": st.get("tunnel_name"),
                       "id": st.get("tunnel_id"), "config": None if st.get("token_file") else str(site_dir(ctx, sid) / "cloudflared.yml")},
            "access": ({"team": st["access"]["team"], "members_file": members.is_file()} if st.get("access") else None),
            "keep_awake": bool(st.get("keep_awake")),
            "service": {"path": str(sp) if sp else None, "installed": bool(sp and sp.exists()), "loaded": loaded,
                        **({"legacy": True} if legacy else {})},
            "log": str(site_dir(ctx, sid) / "log.txt"), "error": st.get("error")}
    if a.json:
        print(json.dumps(info, ensure_ascii=False))
        return
    print(f"Cera site {sid} — {info['status']}" + (f" (pid {info['pid']})" if info["pid"] else "") + f" · {info['url']}")
    t = info["tunnel"]
    print(T(f"  터널       {'원격 관리(토큰 파일)' if t['managed'] == 'remote' else '로컬 관리 ' + str(t['name']) + ' (' + str(t['id']) + ')'}", f"  Tunnel          {'remotely managed (token file)' if t['managed'] == 'remote' else 'locally managed ' + str(t['name']) + ' (' + str(t['id']) + ')'}"))
    if info["access"]:
        print(T(f"  Access     켜짐 — 팀 {info['access']['team']} · members.json {'있음' if info['access']['members_file'] else '없음(통과한 사람 모두 편집)'}", f"  Access          on - team {info['access']['team']} - members.json {'present' if info['access']['members_file'] else 'missing (everyone who passes can edit)'}"))
    else:
        print(T("  Access     꺼짐 — 초대 키로 연다", "  Access          off - opens with the invite key"))
    sv = info["service"]
    print(T(f"  늘 켜 두기  {'설치됨' if sv['installed'] else '설치 안 됨'}" +
          ({True: ' · 올라감', False: ' · 안 올라감', None: ''}[sv['loaded']] if sv["installed"] else " — cera site install --load"), f"  Always-on       {'installed' if sv['installed'] else 'not installed'}" +
          ({True: ' - loaded', False: ' - not loaded', None: ''}[sv['loaded']] if sv["installed"] else " - cera site install --load")))
    print(T(f"  잠자기 막기 {'켜짐(caffeinate -s, 전원 연결 때)' if info['keep_awake'] else '꺼짐'}", f"  Prevent sleep   {'on (caffeinate -s, while on AC power)' if info['keep_awake'] else 'off'}"))
    print(T(f"  로그       {info['log']}", f"  Log             {info['log']}"))
    if info["error"]:
        print(T(f"  오류       {human_error(sid, info['error'])}", f"  Error           {human_error(sid, info['error'])}"))


# ------------------------------------------------------------------ doctor
class Report:
    """doctor output — per line, a human-readable message and what to do. The raw Python / cloudflared text is shown only with --verbose."""

    def __init__(self, out=None, verbose=False):
        self.rows = []
        self.out = out or sys.stdout
        self.verbose = verbose

    def add(self, level, title, detail="", todo="", raw=None):
        self.rows.append((level, title, detail, todo))
        print(f"{level:4}  {title}" + (f" — {detail}" if detail else ""), file=self.out)
        if todo:
            for line in todo.splitlines():
                print(f"      → {line}", file=self.out)
        if raw and self.verbose:
            for line in str(raw).strip().splitlines()[:8]:
                print(T(f"      (원문) {line}", f"      (raw) {line}"), file=self.out)
        self.out.flush()

    @property
    def failed(self):
        return any(r[0] == "FAIL" for r in self.rows)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _is_refused(e) -> bool:
    """Was the connection refused (nothing listening)? Decided from the exception, not from the (translated) text."""
    r = getattr(e, "reason", e)
    return isinstance(r, ConnectionRefusedError) or "Connection refused" in f"{type(r).__name__} {r}"


def _net_words(e) -> str:
    """Connection failure -> human-readable text."""
    r = getattr(e, "reason", e)
    t = f"{type(r).__name__} {r}"
    if isinstance(r, ConnectionRefusedError) or "Connection refused" in t:
        return T("연결 거부 — 그 포트를 듣는 프로그램이 없다", "connection refused - nothing is listening on that port")
    if isinstance(r, socket.timeout) or "timed out" in t:
        return T("시간 초과", "timed out")
    if isinstance(r, socket.gaierror) or "nodename nor servname" in t or "Name or service not known" in t:
        return T("주소(DNS)를 찾지 못했다", "could not resolve the address (DNS)")
    if "CERTIFICATE_VERIFY_FAILED" in t:
        return T("TLS 인증서를 확인하지 못했다", "could not verify the TLS certificate")
    if "SSL" in t or "TLS" in t:
        return T("TLS 연결 오류", "TLS connection error")
    return T("닿지 못했다", "could not connect")


def probe(url: str, timeout=8.0, host: Optional[str] = None) -> dict:
    """-> {code | None, location, body (first 2 KB), words (human-readable), raw (original), refused}. Redirects are not followed."""
    req = urllib.request.Request(url, headers={"User-Agent": "cera-doctor", **({"Host": host} if host else {})})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return {"code": r.status, "location": r.headers.get("Location") or "", "body": r.read(2048), "words": "", "raw": "",
                    "refused": False}
    except urllib.error.HTTPError as e:
        return {"code": e.code, "location": e.headers.get("Location") or "", "body": b"", "words": "", "raw": "", "refused": False}
    except Exception as e:   # noqa: BLE001 — including URLError, OSError, ValueError, and http.client exceptions
        words = _net_words(e)
        return {"code": None, "location": "", "body": b"", "words": words, "raw": f"{type(e).__name__}: {getattr(e, 'reason', e)}",
                "refused": _is_refused(e)}


def http_probe(url: str, timeout=8.0, host: Optional[str] = None):
    """-> (status code | None, Location | human-readable text)."""
    r = probe(url, timeout, host)
    return r["code"], (r["location"] if r["code"] is not None else r["words"])


def check_public(rep: Report, url: str, access: bool, timeout=8.0):
    pr = probe(url.rstrip("/") + "/healthz", timeout=timeout)
    code, extra = pr["code"], pr["location"]
    if code is None:
        rep.add("WARN", T("공개 주소", "public address"), T(f"{url} 에 닿지 못했다({pr['words']})", f"could not reach {url} ({pr['words']})"),
                T("네트워크가 없거나 DNS 가 아직 퍼지지 않았을 수 있다 — 폰(셀룰러)으로 열어 보세요", "There may be no network, or DNS may not have propagated yet - try opening it on a phone (cellular)"), raw=pr["raw"])
    elif access and code in (301, 302, 303, 307) and "cloudflareaccess.com" in extra:
        rep.add("OK", T("공개 주소", "public address"), T("Access 로그인으로 보낸다(정상)", "redirects to the Access login (normal)"))
    elif access and code == 401:
        rep.add("FAIL", T("공개 주소", "public address"), T("터널은 닿지만 Access 앱이 이 주소를 감싸지 않는다(서버가 401 로 막는 중)", "the tunnel is reachable, but no Access application covers this address (the server is blocking with 401)"),
                T("Zero Trust → Access → Applications 에 이 주소의 Self-hosted 앱을 만든다(cera site guide 의 C)", "Create a Self-hosted application for this address under Zero Trust -> Access -> Applications (step C of cera site guide)"))
    elif not access and code == 200:
        rep.add("OK", T("공개 주소", "public address"), T(f"{url} 이 열린다", f"{url} opens"))
    elif code in (502, 503, 504, 530):   # 530 = Cloudflare error 1033 (no tunnel connector), etc.
        rep.add("FAIL", T("공개 주소", "public address"), T(f"HTTP {code} — Cloudflare 까지는 오지만 터널이 이 컴퓨터에 붙어 있지 않다", f"HTTP {code} - requests reach Cloudflare, but the tunnel is not connected to this computer"),
                T("cera site run(또는 site install --load)이 도는지, 로그를 보세요", "Check that cera site run (or site install --load) is running, and look at the log"))
    elif code == 421:
        rep.add("FAIL", T("공개 주소", "public address"), T("421 — 서버가 이 Host 를 모른다(앞단이 Host 를 바꾸는지)", "421 - the server does not know this Host (check whether something in front rewrites Host)"),
                T("state.json 의 allow_hosts 에 그 이름을 더하거나 cloudflared originRequest.httpHostHeader 를 빼세요", "Add that name to allow_hosts in state.json, or remove cloudflared originRequest.httpHostHeader"))
    else:
        rep.add("WARN", T("공개 주소", "public address"), T(f"HTTP {code} — 예상하지 못한 응답", f"HTTP {code} - unexpected response"), T("cera site doctor --verbose 와 log.txt 를 보세요", "See cera site doctor --verbose and log.txt"))


def _cert_hint(err: str) -> str:
    if "CERTIFICATE_VERIFY_FAILED" in err:
        return T("파이썬이 인증서 묶음을 못 찾는다 — python.org 설치판이면 'Install Certificates.command' 를 한 번 실행", "Python cannot find its certificate bundle - if you installed Python from python.org, run 'Install Certificates.command' once")
    return T("네트워크를 확인하세요", "Check your network")


_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ")


def tunnel_restarts(st: dict, log: Path, now: Optional[float] = None, window: float = RESTART_WINDOW) -> int:
    """How many times the tunnel was reopened within the last window seconds — the larger of state's tunnel_starts (written by site run) and the log.txt tail
    ('tunnel exited' lines of the always-on service, only after the last supervisor start)."""
    now = time.time() if now is None else now
    starts = [t for t in (st.get("tunnel_starts") or []) if isinstance(t, (int, float)) and now - t <= window]
    n_state = max(0, len(starts) - 1)
    n_log = 0
    try:
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 64 * 1024))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        m = _TS.match(line)
        if not m:
            continue
        rest = line[m.end():]
        if rest.startswith("server "):   # a new supervisor — do not count the earlier ones
            n_log = 0
            continue
        if rest.startswith("tunnel exited"):
            try:
                t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if now - t <= window:
                n_log += 1
    return max(n_state, n_log)


def run_doctor(ctx, sid, offline=False, out=None, verbose=False) -> Report:
    rep = Report(out, verbose)
    st = ctx.read_state(sid)
    st = st if st and st.get("mode") == "personal" else None
    d = site_dir(ctx, sid)
    cfm = _cf_mod(ctx)

    # 1. cloudflared
    cf = find_cloudflared(st)
    if not cf:
        rep.add("FAIL", "cloudflared", T("없다", "not installed"), T("macOS: brew install cloudflared · 그 밖: Cloudflare 문서의 downloads", "macOS: brew install cloudflared - elsewhere: the downloads page in the Cloudflare docs"))
    else:
        raw = None
        try:
            v = subprocess.run([cf, "--version"], capture_output=True, text=True, timeout=10).stdout.strip() or T("(버전을 읽지 못함)", "(could not read the version)")
        except (OSError, subprocess.TimeoutExpired) as e:
            v, raw = T("(버전을 읽지 못함)", "(could not read the version)"), f"{type(e).__name__}: {e}"
        rep.add("OK", "cloudflared", f"{cf} · {v}", raw=raw)

    # 2. site, login, credentials
    cert = cert_path()
    if not st:
        rep.add("FAIL", T("개인 사이트", "personal site"), T(f"{sid} 가 아직 없다", f"{sid} does not exist yet"), T("cera site init --hostname board.<내도메인>  (순서: cera site guide)", "cera site init --hostname board.<your-domain>  (steps: cera site guide)"))
        if cert.is_file():
            rep.add("OK", T("Cloudflare 로그인", "Cloudflare login"), str(cert))
        else:
            rep.add("FAIL", T("Cloudflare 로그인", "Cloudflare login"), T(f"{cert} 없음", f"{cert} is missing"), T("도메인을 Cloudflare 에 올린 뒤  cloudflared tunnel login  (cera site guide 의 1–2)", "After putting the domain on Cloudflare, run  cloudflared tunnel login  (steps 1-2 of cera site guide)"))
        return rep
    rep.add("OK", T("개인 사이트", "personal site"), T(f"{sid} · https://{st['hostname']}/ · 포트 {st['port']}", f"{sid} - https://{st['hostname']}/ - port {st['port']}"))
    if st.get("token_file"):
        tf = Path(st["token_file"])
        if not tf.is_file():
            rep.add("FAIL", T("터널 토큰 파일", "tunnel token file"), T(f"{tf} 없음", f"{tf} is missing"), T("대시보드에서 토큰을 다시 받아 그 경로에 저장(chmod 600)", "Get the token again from the dashboard and save it at that path (chmod 600)"))
        elif not mode_ok(tf):
            rep.add("FAIL", T("터널 토큰 파일", "tunnel token file"), T(f"{tf} 을 남도 읽을 수 있다", f"{tf} is readable by other users"), f"chmod 600 {tf}")
        else:
            rep.add("OK", T("터널 토큰 파일", "tunnel token file"), f"{tf} (0600)")
    else:
        creds = Path(st.get("credentials_file") or d / "tunnel.json")
        if not creds.is_file():
            rep.add("FAIL", T("터널 자격 파일", "tunnel credentials file"), T(f"{creds} 없음", f"{creds} is missing"), T("cera site init --hostname " + st["hostname"] + " 을 다시", "Run cera site init --hostname " + st["hostname"] + " again"))
        elif not mode_ok(creds):
            rep.add("FAIL", T("터널 자격 파일", "tunnel credentials file"), T(f"{creds} 을 남도 읽을 수 있다", f"{creds} is readable by other users"), f"chmod 600 {creds}")
        else:
            rep.add("OK", T("터널 자격 파일", "tunnel credentials file"), f"{creds} (0600)")
        if cert.is_file():
            rep.add("OK", T("Cloudflare 로그인", "Cloudflare login"), str(cert))
        elif creds.is_file():
            rep.add("WARN", T("Cloudflare 로그인", "Cloudflare login"), T(f"{cert} 없음 — 실행은 되지만 터널 · DNS 를 바꾸려면 필요하다", f"{cert} is missing - it runs without it, but it is needed to change the tunnel or DNS"),
                    "cloudflared tunnel login")
        else:
            rep.add("FAIL", T("Cloudflare 로그인", "Cloudflare login"), T(f"{cert} 없음", f"{cert} is missing"), T("cloudflared tunnel login  (cera site guide 의 2)", "cloudflared tunnel login  (step 2 of cera site guide)"))

    # 3. ingress
    cfg = d / "cloudflared.yml"
    if st.get("token_file"):
        rep.add("WARN", T("경로 설정", "routing config"), T("원격 관리 — 대시보드가 정한다(이 컴퓨터에서 검사할 수 없다)", "remotely managed - the dashboard decides (cannot be checked from this computer)"),
                T(f"Public Hostname: {st['hostname']} → http://127.0.0.1:{st['port']} 인지 대시보드에서 확인", f"Check in the dashboard that the Public Hostname is {st['hostname']} -> http://127.0.0.1:{st['port']}"))
    elif not cfg.is_file():
        rep.add("FAIL", T("경로 설정", "routing config"), T(f"{cfg} 없음", f"{cfg} is missing"), T("cera site init 을 다시", "Run cera site init again"))
    elif cf:
        try:
            _cf([cf, "tunnel", "--config", str(cfg), "ingress", "validate"], "ingress validate", timeout=30)
            rule = _cf([cf, "tunnel", "--config", str(cfg), "ingress", "rule", f"https://{st['hostname']}/"], "ingress rule", timeout=30)
            if "#0" in rule:
                rep.add("OK", T("경로 설정", "routing config"), T(f"ingress validate 통과 · https://{st['hostname']}/ → 규칙 #0", f"ingress validate passed - https://{st['hostname']}/ -> rule #0"))
            else:
                rep.add("FAIL", T("경로 설정", "routing config"), T(f"https://{st['hostname']}/ 이 첫 규칙에 맞지 않는다", f"https://{st['hostname']}/ does not match the first rule"), T("cera site init 을 다시", "Run cera site init again"))
        except StepError as e:
            rep.add("FAIL", T("경로 설정", "routing config"), T("cloudflared 가 설정 파일을 받아들이지 않는다", "cloudflared does not accept the config file"),
                    T(f"cera site init 을 다시(파일: {cfg}) · 까닭 원문: cera site doctor --verbose", f"Run cera site init again (file: {cfg}) - raw reason: cera site doctor --verbose"), raw=str(e))

    # 4. tunnel connection (requires login and network)
    if not offline and cf and not st.get("token_file") and cert.is_file() and st.get("tunnel_name"):
        try:
            out = _cf([cf, "tunnel", "--origincert", str(cert), "info", "--output", "json", st["tunnel_name"]], "tunnel info", timeout=20)
            conns = (json.loads(out) or {}).get("conns") or []
            n = sum(len(c.get("conns") or []) for c in conns if isinstance(c, dict))
            if n:
                rep.add("OK", T("터널 연결", "tunnel connection"), T(f"연결기 {len(conns)}개 · 연결 {n}개", f"{len(conns)} connector(s) - {n} connection(s)"))
            else:
                rep.add("WARN", T("터널 연결", "tunnel connection"), T("연결이 없다", "no connections"), T("cera site run 또는 site install --load", "Run cera site run or site install --load"))
        except (StepError, ValueError, AttributeError, TypeError) as e:
            rep.add("WARN", T("터널 연결", "tunnel connection"), T("확인하지 못했다 — 로그인(cert.pem) · 네트워크를 확인", "could not check - check the login (cert.pem) and the network"),
                    T("cloudflared tunnel info " + st["tunnel_name"] + "  로 직접 볼 수 있다", "You can look yourself with cloudflared tunnel info " + st["tunnel_name"]), raw=str(e))

    # 5. the server on this computer
    port = int(st.get("port") or DEFAULT_PORT)
    ours = st.get("status") in ("running", "starting") and ctx.alive(st.get("pid"))
    pr = probe(f"http://127.0.0.1:{port}/healthz", timeout=3, host=f"127.0.0.1:{port}")
    code = pr["code"]
    healthy = code == 200 and pr["body"].replace(b" ", b"").startswith(b'{"ok":true')
    other = not ours and (code is not None or (not pr["refused"] and port_in_use(port)))
    if other:   # our supervisor is off but someone answers on that port — do not prescribe run
        alt = free_port(port + 1)
        rep.add("FAIL", T("이 컴퓨터의 서버", "server on this computer"), T(f"포트 {port}: 다른 프로그램이 쓰는 중" + (f"(응답 {code})" if code else ""), f"port {port}: another program is using it" + (f" (response {code})" if code else "")),
                T(f"{port_fix_cmd(sid, alt)}   ← 이 사이트의 포트를 바꾼다(→ {alt})\n"
                f"그 뒤 cera site run{_idflag(sid)}  · 어떤 프로그램인지: lsof -nP -iTCP:{port} -sTCP:LISTEN", f"{port_fix_cmd(sid, alt)}   <- changes this site's port (-> {alt})\n"
                f"Then cera site run{_idflag(sid)}  - which program it is: lsof -nP -iTCP:{port} -sTCP:LISTEN"), raw=pr["raw"])
    elif not ours:
        rep.add("FAIL", T("이 컴퓨터의 서버", "server on this computer"), T("꺼져 있다", "not running"),
                T(f"cera site run{_idflag(sid)}(시험) 또는 cera site install --load · 로그: {d / 'log.txt'}", f"cera site run{_idflag(sid)} (to try it) or cera site install --load - log: {d / 'log.txt'}"), raw=pr["raw"])
    elif not healthy:
        rep.add("FAIL", T("이 컴퓨터의 서버", "server on this computer"), T(f"site run(pid {st.get('pid')})은 도는데 포트 {port} 가 제대로 답하지 않는다"
                + (f"(응답 {code})" if code else f"({pr['words']})"), f"site run (pid {st.get('pid')}) is running but port {port} is not answering properly"
                + (f" (response {code})" if code else f" ({pr['words']})")),
                T(f"로그를 보세요: {d / 'log.txt'} · 다시 켜기: cera stop {sid} 뒤 site run", f"See the log: {d / 'log.txt'} - to restart: cera stop {sid}, then site run"), raw=pr["raw"])
    else:
        rep.add("OK", T("이 컴퓨터의 서버", "server on this computer"), T(f"127.0.0.1:{port} 응답", f"127.0.0.1:{port} answers"))
        code2, _ = http_probe(f"http://127.0.0.1:{port}/healthz", timeout=3, host=st["hostname"])
        want = 401 if st.get("access") else 200
        if code2 == want:
            rep.add("OK", T("공개 Host 규칙", "public Host rule"), T(f"Host: {st['hostname']} → {code2}" + (" (JWT 없으면 막힘 — 정상)" if want == 401 else ""), f"Host: {st['hostname']} -> {code2}" + (" (blocked without a JWT - normal)" if want == 401 else "")))
        else:
            rep.add("FAIL", T("공개 Host 규칙", "public Host rule"), T(f"Host: {st['hostname']} → {code2}(기대 {want}) — 도는 서버가 옛 설정이다", f"Host: {st['hostname']} -> {code2} (expected {want}) - the running server has old settings"),
                    T("다시 켜세요(전경: Ctrl+C 뒤 site run · 서비스: cera site install --load)", "Restart it (foreground: Ctrl+C, then site run - service: cera site install --load)"))

    # 5b. a tunnel that keeps dying
    n = tunnel_restarts(st, d / "log.txt")
    if n >= RESTART_WARN:
        rep.add("WARN", T("터널 다시 열기", "tunnel reopening"), T(f"최근 {RESTART_WINDOW // 60}분에 {n}번 끊겨 다시 열었다", f"the tunnel dropped and was reopened {n} time(s) in the last {RESTART_WINDOW // 60} minutes"),
                T(f"로그 보기: tail -n 40 {d / 'log.txt'}(늘 켜 두기) 또는 site run 화면\n"
                "자격 · 토큰이 맞는지(site init 다시), 같은 터널을 다른 컴퓨터가 쓰는지, 네트워크를 확인", f"See the log: tail -n 40 {d / 'log.txt'} (always-on service) or the site run screen\n"
                "Check that the credentials/token are correct (run site init again), whether another computer uses the same tunnel, and the network"))

    # 6. public address
    if offline:
        rep.add("WARN", T("공개 주소", "public address"), T("건너뜀(--offline)", "skipped (--offline)"))
    else:
        check_public(rep, f"https://{st['hostname']}", bool(st.get("access")))

    # 7. Access
    acc = st.get("access")
    if acc:
        if st.get("trust_header"):
            rep.add("FAIL", "trust_header", T("Access 와 함께 쓸 수 없다(머리는 위조된다)", "cannot be used together with Access (the header can be forged)"), T("state.json 에서 trust_header 를 지우세요", "Remove trust_header from state.json"))
        if not offline:
            try:
                j = cfm.Jwks(acc.get("jwks_url") or cfm.certs_url(acc["team"]))
            except ValueError as e:
                j = None
                rep.add("FAIL", T("Access 키(JWKS)", "Access keys (JWKS)"), T("state.json 의 access.jwks_url 이 https 가 아니다", "access.jwks_url in state.json is not https"),
                        T("state.json 의 access.jwks_url 을 지우세요", "Remove access.jwks_url from state.json"), raw=str(e))
            if j is None:
                pass
            elif j.refresh(force=True):
                rep.add("OK", T("Access 키(JWKS)", "Access keys (JWKS)"), T(f"{acc['team']}.cloudflareaccess.com · 키 {len(j.kids)}개", f"{acc['team']}.cloudflareaccess.com - {len(j.kids)} key(s)"))
            elif "404" in (j.last_error or ""):
                rep.add("FAIL", T("Access 키(JWKS)", "Access keys (JWKS)"), T(f"{acc['team']}.cloudflareaccess.com 이 없다 — 팀 이름이 틀렸다", f"{acc['team']}.cloudflareaccess.com does not exist - the team name is wrong"),
                        T("Zero Trust → Settings 의 Team domain 을 보고 site init --access-team 을 다시", "Check the Team domain under Zero Trust -> Settings, then run site init --access-team again"), raw=j.last_error)
            else:
                rep.add("WARN", T("Access 키(JWKS)", "Access keys (JWKS)"), T(f"받지 못했다({_net_words(Exception(j.last_error or ''))})", f"could not fetch them ({_net_words(Exception(j.last_error or ''))})"),
                        _cert_hint(j.last_error or ""), raw=j.last_error)
        m = d / "members.json"
        mm = cfm.Members(m)
        err = mm.check()
        if not m.is_file():
            rep.add("OK", "members.json", T("없음 — Access 를 통과한 사람은 모두 편집", "missing - everyone who passes Access can edit"))
        elif err:
            rep.add("FAIL", "members.json", T(f"틀렸다 — 지금은 공개 주소로 아무도 못 들어온다: {err}", f"malformed - nobody can get in through the public address right now: {err}"),
                    T(f"{m} 을 고치세요. 형식: {cfm.MEMBERS_EXAMPLE}  (모두 들이려면 \"edit\": [\"*\"])", f"Fix {m}. Format: {cfm.MEMBERS_EXAMPLE}  (to admit everyone: \"edit\": [\"*\"])"),
                    raw=getattr(mm, "error_raw", None))
        else:
            c = mm.counts()
            rep.add("OK", "members.json", T(f"편집 {c['edit']} · 보기 {c['view']} · 에이전트 {c['agents']}", f"edit {c['edit']} - view {c['view']} - agents {c['agents']}"))

    # 8. always-on
    plat = platform_name()
    if plat:
        sp, legacy = installed_service(sid, plat)
        if legacy:
            rep.add("WARN", T("늘 켜 두기", "always-on service"), T(f"옛 이름(tempboard)의 서비스 {sp.name} 가 깔려 있다", f"the old-named (tempboard) service {sp.name} is in place"),
                    T("cera site install --load 로 새 이름으로 바꾼다(옛 서비스는 지운다)", "Run cera site install --load to switch to the new name (the old service is removed)"))
        if not sp.exists():
            rep.add("WARN", T("늘 켜 두기", "always-on service"), T("설치 안 됨", "not installed"), "cera site install --load")
        else:
            missing = []
            if plat == "macos":
                try:
                    args = plistlib.loads(sp.read_bytes()).get("ProgramArguments") or []
                except Exception:   # noqa: BLE001
                    args = []
                missing = [x for x in args[:2] if not Path(x).exists()] if args else [T("(ProgramArguments 를 읽지 못함)", "(could not read ProgramArguments)")]
            if missing:
                rep.add("FAIL", T("늘 켜 두기", "always-on service"), T(f"서비스가 가리키는 파일이 없다: {', '.join(missing)}(파이썬 · Cera 가 옮겨졌다)", f"the file(s) the service points to are missing: {', '.join(missing)} (Python or Cera was moved)"),
                        T("cera site install --load 를 다시", "Run cera site install --load again"))
            else:
                loaded = service_loaded(sid, plat, legacy)
                if loaded:
                    rep.add("OK", T("늘 켜 두기", "always-on service"), T(f"{sp.name} · 올라감", f"{sp.name} - loaded"))
                else:
                    rep.add("WARN", T("늘 켜 두기", "always-on service"), T(f"{sp.name} · 올라가 있지 않다", f"{sp.name} - not loaded"), "cera site install --load")
            if plat == "linux":
                try:
                    lin = subprocess.run(["loginctl", "show-user", os.environ.get("USER", ""), "--property=Linger"],
                                         capture_output=True, text=True, timeout=5).stdout
                    if "Linger=yes" not in lin:
                        rep.add("WARN", T("로그아웃 뒤에도", "after logout"), T("linger 꺼짐 — 로그아웃하면 멈춘다", "linger is off - it stops when you log out"), "loginctl enable-linger $USER")
                except (OSError, subprocess.TimeoutExpired):
                    pass

    # 9. sleep (macOS)
    if sys.platform == "darwin":
        try:
            pm = subprocess.run(["pmset", "-g"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            pm = ""
        m_ = re.search(r"^\s*sleep\s+(\d+)", pm, re.M)
        if st.get("keep_awake"):
            rep.add("OK", T("잠자기", "sleep"), T("site run 이 caffeinate -s 로 막는다(전원 연결 때만 · 덮개를 닫으면 잘 수 있다)", "site run prevents it with caffeinate -s (only on AC power - it may still sleep if you close the lid)"))
        elif m_ and m_.group(1) != "0":
            rep.add("WARN", T("잠자기", "sleep"), T(f"{m_.group(1)}분 뒤 잠든다 — 잠들면 주소가 꺼진다", f"it sleeps after {m_.group(1)} minutes - the address goes down while it sleeps"),
                    T("site init --keep-awake, 또는 시스템 설정 → 에너지에서 자동 잠자기 끄기(늘 켜 둘 Mac mini 가 가장 좋다)", "Use site init --keep-awake, or turn off automatic sleep under System Settings -> Energy (a machine that stays on, such as a Mac mini, is best)"))
        else:
            rep.add("OK", T("잠자기", "sleep"), T("자동 잠자기 꺼짐", "automatic sleep is off"))
    return rep


def cmd_doctor(ctx, a):
    sid = check_id(a.id)
    rep = run_doctor(ctx, sid, offline=a.offline, verbose=a.verbose)
    n = {k: sum(1 for r in rep.rows if r[0] == k) for k in ("OK", "WARN", "FAIL")}
    print(f"— OK {n['OK']} · WARN {n['WARN']} · FAIL {n['FAIL']}")
    sys.exit(1 if rep.failed else 0)


def cmd_guide(ctx, a):
    print(guide_text(ctx, check_id(a.id), DEFAULT_PORT))


# ------------------------------------------------------------------ CLI wiring
def add_parser(sub, ctx):
    """Argument definitions for `cera site ...` (when wiring directly into bin, calling just this one is enough)."""
    s = sub.add_parser("site", help=T("개인 보드 웹 — 자기 도메인 · 이름 있는 터널 · (선택) Cloudflare Access · 늘 켜 두기", "personal board on your own domain - named tunnel - (optional) Cloudflare Access - always-on service"),
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ss = s.add_subparsers(dest="site_cmd", required=True)

    def with_id(p):
        p.add_argument("--id", default=DEFAULT_ID, help=T(f"사이트 id(기본 {DEFAULT_ID})", f"site id (default {DEFAULT_ID})"))
        return p

    p = with_id(ss.add_parser("init", help=T("터널 만들기 · DNS 연결 · 설정 파일(cert.pem 이 없으면 할 일만 알려 주고 끝, exit 2)", "create the tunnel, route DNS, write config files (without cert.pem it only prints what to do and exits with code 2)")))
    p.add_argument("--hostname", required=True, help=T("공개 주소(예: board.example.com) — 그 도메인이 Cloudflare 에 있어야 한다", "public address (e.g. board.example.com) - the domain must be on Cloudflare"))
    p.add_argument("--port", type=int, default=None,
                   help=T(f"이 컴퓨터의 포트(기본 {DEFAULT_PORT} — 다른 프로그램이 쓰고 있으면 빈 포트를 골라 알린다)", f"port on this computer (default {DEFAULT_PORT} - if another program uses it, a free port is picked and reported)"))
    p.add_argument("--tunnel-name", default=None, help=T("터널 이름(기본 cera-<hostname 의 점을 - 로>)", "tunnel name (default cera-<hostname with dots replaced by ->)"))
    p.add_argument("--reuse-tunnel", action="store_true",
                   help=T("같은 이름의 터널이 계정에 이미 있으면 이어받는다(다른 컴퓨터에서 옮길 때만 — 두 곳에서 같이 돌리면 요청이 갈린다)", "take over a tunnel with the same name if it already exists in the account (only when moving from another computer - running it in two places splits requests)"))
    p.add_argument("--token-file", default=None, help=T("원격 관리 터널 토큰 파일(0600) — 주면 create · route 를 건너뛴다", "remotely managed tunnel token file (0600) - if given, create and route are skipped"))
    p.add_argument("--access-team", default=None, help=T("Cloudflare Access 팀 이름(<팀>.cloudflareaccess.com)", "Cloudflare Access team name (<team>.cloudflareaccess.com)"))
    p.add_argument("--access-aud", default=None, help=T("Access 앱의 Application Audience (AUD) Tag", "Application Audience (AUD) Tag of the Access application"))
    p.add_argument("--no-access", action="store_true", help=T("Access 를 끈다(초대 키로)", "turn Access off (use invite keys)"))
    p.add_argument("--overwrite-dns", action="store_true", help=T("이미 있는 DNS 레코드를 덮어쓴다", "overwrite an existing DNS record"))
    p.add_argument("--view-link", action="store_true", help=T("보기 전용 초대 키도 만든다", "also create a view-only invite key"))
    p.add_argument("--keep-awake", dest="keep_awake", action="store_true", default=None, help=T("site run 이 잠자기를 막는다(macOS caffeinate -s)", "site run prevents sleep (macOS caffeinate -s)"))
    p.add_argument("--no-keep-awake", dest="keep_awake", action="store_false")
    p.add_argument("--title", default=None)
    p.set_defaults(f=lambda a: cmd_init(ctx, a))

    p = with_id(ss.add_parser("run", help=T("전경 감독 — 서버 + cloudflared(무제한 다시 열기). ", "foreground supervision - server + cloudflared (reopened without limit). ") + stop_howto(),
                              description=T("전경 감독 — 서버 + cloudflared(무제한 다시 열기).\n", "Foreground supervision - server + cloudflared (reopened without limit).\n") + stop_howto()))
    p.set_defaults(f=lambda a: cmd_run(ctx, a))
    p = with_id(ss.add_parser("port", help=T("이 사이트의 포트만 바꾸기(다른 프로그램이 쓰고 있을 때) — 터널 · 키는 그대로", "change only this site's port (when another program is using it) - tunnel and keys stay")))
    p.add_argument("port", nargs="?", default="auto", help=T("새 포트(기본 auto = 비어 있는 포트)", "new port (default auto = a free port)"))
    p.set_defaults(f=lambda a: cmd_port(ctx, a))

    for name, fn, hl in (("install", cmd_install, T("늘 켜 두기 파일 쓰기(--load 면 launchctl · systemctl 로 올린다)", "write the always-on service file (with --load, load it via launchctl / systemctl)")),
                         ("uninstall", cmd_uninstall, T("늘 켜 두기 파일 지우기(--load 면 내린다)", "delete the always-on service file (with --load, unload it)"))):
        p = with_id(ss.add_parser(name, help=hl))
        p.add_argument("--dry-run", action="store_true", help=T("쓰지 않고 내용과 명령만 보인다", "show the content and commands without writing anything"))
        flags = ("--load",) if name == "install" else ("--load", "--unload")   # --unload is only for taking it down (uninstall)
        p.add_argument(*flags, dest="load", action="store_true", help=T("launchctl bootstrap/bootout · systemctl --user 까지 실행", "also run launchctl bootstrap/bootout / systemctl --user"))
        p.add_argument("--platform", choices=["macos", "linux"], default=None, help=argparse.SUPPRESS)
        p.set_defaults(f=(lambda fn: (lambda a: fn(ctx, a)))(fn))

    p = with_id(ss.add_parser("status", help=T("상태 한눈에", "status at a glance")))
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=lambda a: cmd_status(ctx, a))
    p = with_id(ss.add_parser("doctor", help=T("운영 점검 — 줄마다 OK|WARN|FAIL 과 할 일(FAIL 이 있으면 exit 1)", "health check - each line is OK|WARN|FAIL with what to do (exit 1 if anything fails)")))
    p.add_argument("--offline", action="store_true", help=T("네트워크가 필요한 점검(공개 주소 · JWKS · 터널 연결)은 건너뛴다", "skip the checks that need the network (public address, JWKS, tunnel connection)"))
    p.add_argument("--verbose", action="store_true", help=T("파이썬 · cloudflared 원문 오류도 보인다", "also show the raw Python / cloudflared errors"))
    p.set_defaults(f=lambda a: cmd_doctor(ctx, a))
    p = with_id(ss.add_parser("links", help=T("링크(Access 모드면 키 없는 공개 주소 + 이 컴퓨터 주소)", "links (in Access mode: the public address without a key plus this computer's address)")))
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=lambda a: cmd_links(ctx, a))
    p = with_id(ss.add_parser("guide", help=T("사람이 할 일(도메인 · 로그인 · Access) 순서", "what you do by hand, in order (domain, login, Access)")))
    p.set_defaults(f=lambda a: cmd_guide(ctx, a))
    return s


def register(sub, ctx):
    """Called by the extension slot (EXT_MODULES) in bin/cera."""
    add_parser(sub, ctx)
