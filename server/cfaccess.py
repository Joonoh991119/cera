# SPDX-License-Identifier: MIT
"""Cloudflare Access verification (standard library only) — the auth-chain (authn) entry `CfAccess`.

When the personal board web (`tempboard site ...`) is wrapped in Cloudflare Access, the server trusts **only** the
`Cf-Access-Jwt-Assertion` header on the original request. It ignores the email header (`Cf-Access-Authenticated-User-Email`)
and the cookie (`CF_Authorization`) — those can be forged by another process on the same machine or via DNS rebinding.
A signature cannot be produced without Cloudflare's private key.

Verification order (docs: developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
  (1) format (two dots, base64url, JSON) (2) `alg == "RS256"` only (prevents `none` / HS256 confusion) (3) JWKS key by `kid`
  (4) PKCS#1 v1.5 signature — build the entire expected byte string and compare (no room for parse-based forgery) (5) `iss` matches exactly
  (6) the configured value is in the `aud` array (7) `exp` / `nbf` within +/-leeway (default 60 s).
The JWKS (`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`) is cached for 1 hour. An unknown kid triggers a refetch at most
once per 60 seconds, and if the fetch fails the previous cache is kept (Access keys rotate every 6 weeks and the old key stays valid for 7 more days).

Roles (`sites/<id>/members.json`; if absent, everyone who passes Access is edit):
    {"edit": ["me@x.com", "@example.org"], "view": ["guest@y.com"],
     "agents": {"<service token Client ID>": {"name": "codex@lab-server", "role": "edit"}}}
  - Emails are case-insensitive. "@domain" matches every address in that domain. Listed in both edit and view -> edit.
  - File exists but the person is not listed -> 403 (authenticated but not authorized). A broken file admits nobody (fail closed).
  - A service token (the JWT has no email and `common_name` = Client ID) is accepted only if listed in `agents` -> `agent: True` on the user.

Its place in the chain: only requests with a public Host (rule is public) are checked. A missing or invalid JWT gives `Auth(deny=True)` (401, no fallback
to invite key / cookie). Requests with a local Host (127.0.0.1 or localhost, and the peer is also loopback) get None and follow the existing invite-key rules
— the owner's local browser, and agent tokens on the same machine.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import importlib.util
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

HERE = Path(__file__).resolve().parent
HEADER = "Cf-Access-Jwt-Assertion"
EDGE_HEADERS = ("Cf-Ray", "Cf-Connecting-IP")   # headers the Cloudflare edge adds to the original request
# SHA-256 DigestInfo prefix (RFC 8017 §9.2 note 1)
DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
MAX_TOKEN = 16 * 1024
MAX_JWKS = 1024 * 1024
MIN_BITS = 2048
TEAM_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
AUD_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,256}$")
_B64U = re.compile(r"^[A-Za-z0-9_-]*$")
# A non-https jwks_url (local fake JWKS) is for tests only — accepted only when this environment variable is set to "1"
INSECURE_JWKS_ENV = "TEMPBOARD_TEST_INSECURE_JWKS"


def T(ko, en):
    """Korean or English text (server/i18n.py: TEMPBOARD_LANG > `tempboard lang` > locale > English)."""
    key = "tempboard_i18n"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "i18n.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key].T(ko, en)


def _security():
    """The same security module object as server.py (`tempboard_security`) — shares the Auth class."""
    key = "tempboard_security"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "security.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


class JwtError(ValueError):
    """Verification failure — the reason is a short English word (for tests and logs). The user only sees a 401."""


def b64u(s) -> bytes:
    if isinstance(s, bytes):
        s = s.decode("ascii")
    if not _B64U.match(s):
        raise JwtError("b64")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError):
        raise JwtError("b64")


def rs256_ok(n: int, e: int, signing_input: bytes, sig: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 + SHA-256. The block recovered from the signature must equal the expected block (00 01 FF...FF 00 DigestInfo hash)
    byte for byte — the block is never parsed, so Bleichenbacher '06-style forgery (low e, lenient padding parsing) is impossible."""
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    s = int.from_bytes(sig, "big")
    if s >= n:
        return False
    m = pow(s, e, n).to_bytes(k, "big")
    want = b"\x00\x01" + b"\xff" * (k - 3 - len(DIGEST_INFO) - 32) + b"\x00" + DIGEST_INFO + hashlib.sha256(signing_input).digest()
    return hmac.compare_digest(m, want)


def parse_jwks(doc) -> Dict[str, Tuple[int, int]]:
    """JWKS (JSON) -> {kid: (n, e)}. Only keys that are RSA, >= 2048 bits, odd e >= 3, use=sig (if present), and alg=RS256."""
    out = {}
    keys = doc.get("keys") if isinstance(doc, dict) else None
    for k in keys if isinstance(keys, list) else []:
        try:
            if not isinstance(k, dict) or k.get("kty") != "RSA" or not isinstance(k.get("kid"), str):
                continue
            if k.get("use", "sig") != "sig" or k.get("alg", "RS256") != "RS256":
                continue
            n = int.from_bytes(b64u(k["n"]), "big")
            e = int.from_bytes(b64u(k["e"]), "big")
            if n.bit_length() < MIN_BITS or e < 3 or e % 2 == 0:
                continue
            out[k["kid"]] = (n, e)
        except (KeyError, TypeError, JwtError):
            continue
    return out


def decode_unverified(token: str):
    """(header, payload, signing input, signature) — the signature has not been checked yet. JwtError if the format is wrong."""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN or token.count(".") != 2:
        raise JwtError("format")
    h64, p64, s64 = token.split(".")
    try:
        h = json.loads(b64u(h64).decode("utf-8"))
        p = json.loads(b64u(p64).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise JwtError("format") from e
    if not isinstance(h, dict) or not isinstance(p, dict):
        raise JwtError("format")
    return h, p, f"{h64}.{p64}".encode("ascii"), b64u(s64)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def verify(token: str, key_for: Callable[[str], Optional[Tuple[int, int]]], aud, iss: str,
           now: Optional[float] = None, leeway: float = 60.0) -> dict:
    """Verify the JWT and return its payload (dict). key_for(kid) -> (n, e) | None. aud: a single string or a list (any match suffices)."""
    h, p, signing_input, sig = decode_unverified(token)
    if h.get("alg") != "RS256":       # reject 'none', 'HS256' (public key used as an HMAC key), 'RS512', ... all of them
        raise JwtError("alg")
    if "crit" in h:                    # reject unknown critical extensions (RFC 7515 §4.1.11)
        raise JwtError("crit")
    kid = h.get("kid")
    if not isinstance(kid, str) or not kid:
        raise JwtError("kid")
    key = key_for(kid)
    if key is None:
        raise JwtError("kid")
    n, e = key
    if not rs256_ok(n, e, signing_input, sig):
        raise JwtError("sig")
    if p.get("iss") != iss:
        raise JwtError("iss")
    want = [aud] if isinstance(aud, str) else list(aud)
    got = p.get("aud")
    got = got if isinstance(got, list) else [got]
    if not any(isinstance(a, str) and a in want for a in got):
        raise JwtError("aud")
    t = time.time() if now is None else now
    if not _num(p.get("exp")) or p["exp"] + leeway < t:
        raise JwtError("exp")
    if "nbf" in p and (not _num(p["nbf"]) or p["nbf"] - leeway > t):
        raise JwtError("nbf")
    return p


def team_name(team: str) -> str:
    """'myteam', 'myteam.cloudflareaccess.com', 'https://myteam.cloudflareaccess.com/' -> 'myteam'. ValueError if malformed."""
    t = str(team or "").strip().lower()
    t = re.sub(r"^https?://", "", t).rstrip("/")
    if t.endswith(".cloudflareaccess.com"):
        t = t[: -len(".cloudflareaccess.com")]
    if not TEAM_RE.match(t):
        raise ValueError(T(f"Access 팀 이름을 읽지 못했다: {team!r} (예: myteam — <팀>.cloudflareaccess.com 의 앞부분)",
                           f"Cannot read the Access team name: {team!r} (e.g. myteam - the first part of <team>.cloudflareaccess.com)"))
    return t


def check_aud(aud: str) -> str:
    a = str(aud or "").strip()
    if not AUD_RE.match(a):
        raise ValueError(T(f"Access AUD 태그를 읽지 못했다: {aud!r} (대시보드 앱의 Application Audience (AUD) Tag 를 그대로 복사)",
                           f"Cannot read the Access AUD tag: {aud!r} (copy the Application Audience (AUD) Tag from the dashboard app as is)"))
    return a


def issuer(team: str) -> str:
    return f"https://{team_name(team)}.cloudflareaccess.com"


def certs_url(team: str) -> str:
    return issuer(team) + "/cdn-cgi/access/certs"


def _log(*a):
    print(time.strftime("%F %T"), "cfaccess:", *a, file=sys.stderr, flush=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Fetch the JWKS only from the configured address — do not follow redirects (to another host, or https->http); a 3xx raises HTTPError."""

    def redirect_request(self, *a, **k):
        return None


def check_jwks_url(url: str) -> str:
    """https only. http (local fake JWKS) only when TEMPBOARD_TEST_INSECURE_JWKS=1 (for tests). file: etc. are always rejected."""
    u = str(url or "")
    scheme = u.split(":", 1)[0].lower()
    if scheme == "https":
        return u
    if scheme == "http" and os.environ.get(INSECURE_JWKS_ENV) == "1":
        return u
    raise ValueError(T(f"JWKS 주소는 https 만 받는다: {u!r} (http 는 시험에서 {INSECURE_JWKS_ENV}=1 일 때만)",
                       f"The JWKS URL must be https: {u!r} (http only in tests with {INSECURE_JWKS_ENV}=1)"))


class Jwks:
    """JWKS cache. Refetch when ttl (1 hour) expires or an unknown kid arrives, but attempt at most once per gap (60 s).
    If the fetch fails, the previous cache is kept. On the very first fetch (cache empty), other requests wait until it finishes."""

    def __init__(self, url: str, ttl: float = 3600.0, gap: float = 60.0, timeout: float = 5.0,
                 clock: Callable[[], float] = time.time, opener=None):
        self.url, self.ttl, self.gap, self.timeout, self.clock = check_jwks_url(url), ttl, gap, timeout, clock
        self._keys: Dict[str, Tuple[int, int]] = {}
        self._fetched_at = float("-inf")
        self._tried_at = float("-inf")
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._opener = opener
        self.fetches = 0          # for tests and doctor — how many times it actually went to fetch
        self.last_error: Optional[str] = None

    def _get(self) -> dict:
        req = urllib.request.Request(self.url, headers={"User-Agent": "tempboard", "Accept": "application/json"})
        if self._opener:
            open_ = self._opener
        else:
            handlers = [_NoRedirect(), urllib.request.ProxyHandler({})]
            if self.url.startswith("https:"):
                handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            open_ = urllib.request.build_opener(*handlers).open
        with open_(req, timeout=self.timeout) as r:
            data = r.read(MAX_JWKS + 1)
        if len(data) > MAX_JWKS:
            raise ValueError(T("JWKS 가 너무 크다", "The JWKS is too large"))
        return json.loads(data.decode("utf-8"))

    def refresh(self, force: bool = False) -> bool:
        """Refetch (respecting the gap between attempts). True if new keys were obtained."""
        first = not self._keys
        if not self._fetch_lock.acquire(blocking=first, timeout=self.timeout * 2 if first else -1):
            return False   # another request is fetching — use the previous cache
        try:
            now = self.clock()
            with self._lock:
                if not force and now - self._tried_at < self.gap:
                    return False
                self._tried_at = now
            self.fetches += 1
            try:
                keys = parse_jwks(self._get())
            except Exception as e:   # noqa: BLE001 — including HTTPException such as IncompleteRead (so it does not leak into the request handler)
                self.last_error = f"{type(e).__name__}: {e}"
                _log(T("JWKS 받기 실패(앞 캐시 유지):", "JWKS fetch failed (keeping the previous cache):"), self.last_error)
                return False
            if not keys:
                self.last_error = T("JWKS 에 쓸 수 있는 RSA 키가 없다", "The JWKS has no usable RSA key")
                _log(self.last_error)
                return False
            with self._lock:
                self._keys, self._fetched_at = keys, now
            self.last_error = None
            return True
        finally:
            self._fetch_lock.release()

    def key(self, kid: str) -> Optional[Tuple[int, int]]:
        now = self.clock()
        with self._lock:
            k = self._keys.get(kid)
            stale = now - self._fetched_at > self.ttl
        if k is None or stale:
            self.refresh()
            with self._lock:
                k = self._keys.get(kid)
        return k

    @property
    def kids(self):
        with self._lock:
            return sorted(self._keys)


def _people(raw: dict, key: str) -> list:
    """The edit / view field — [] if absent; if present it must be a list of strings (a single string or a number means a broken file)."""
    if key not in raw or raw[key] is None:
        return []
    v = raw[key]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError(T(f'"{key}" 는 이메일 목록이어야 한다 — 예: "{key}": ["me@x.com", "@example.org"] (따옴표 문자열 하나가 아니라 [ ])',
                           f'"{key}" must be a list of emails - e.g. "{key}": ["me@x.com", "@example.org"] (a list [ ], not a single quoted string)'))
    out = []
    for x in v:
        e = x.strip().lower()
        if not e:
            continue
        if e != "*" and "@" not in e:
            raise ValueError(T(f'"{key}" 의 {x!r} 를 읽지 못했다 — 이메일 · "@도메인" · "*"(Access 를 통과한 모두) 중 하나',
                               f'Cannot read {x!r} in "{key}" - use an email, "@domain", or "*" (everyone who passes Access)'))
        out.append(e)
    return out


MEMBERS_EXAMPLE = '{"edit": ["me@example.com"], "view": [], "agents": {"<Client ID>": {"name": "codex@lab-server", "role": "edit"}}}'


class Members:
    """Read members.json and decide roles. Re-reads when the file's (inode, mtime, size) changes (no server restart needed).
    No file -> every person is edit, service tokens are rejected. Broken file (format, string field, empty person list) -> reject everyone (fail closed)
    + the reason in error (server log, `site doctor` FAIL). "*" means everyone who passed Access."""

    def __init__(self, path: Optional[Path]):
        self.path = Path(path) if path else None
        self._mark = None
        self._data: Optional[dict] = None
        self.error: Optional[str] = None       # human-readable (doctor, log)
        self.error_raw: Optional[str] = None   # raw text (doctor --verbose)
        self._lock = threading.Lock()

    def _load(self):
        if self.path is None:
            return None
        try:
            s = self.path.stat()
            mark = (s.st_ino, s.st_mtime_ns, s.st_size)
        except OSError:
            mark = None
        with self._lock:
            if mark == self._mark and (self._data is not None or mark is None or self.error):
                return self._data
            self._mark = mark
            if mark is None:
                self._data, self.error, self.error_raw = None, None, None
                return None
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError(T("맨 위가 {…} 가 아니다", "The top level is not {…}"))
                unknown = sorted(set(raw) - {"edit", "view", "agents"})
                if unknown:
                    raise ValueError(T(f"모르는 칸 {unknown} — edit · view · agents 만 쓴다", f"Unknown fields {unknown} - only edit, view, agents are used"))
                agents = raw.get("agents") or {}
                if not isinstance(agents, dict) or not all(isinstance(v, dict) for v in agents.values()):
                    raise ValueError(T('"agents" 는 {"<Client ID>": {"name": …, "role": "edit"|"view"}} 이어야 한다', '"agents" must be {"<Client ID>": {"name": …, "role": "edit"|"view"}}'))
                for cid, v in agents.items():
                    if v.get("role", "edit") not in ("edit", "view"):
                        raise ValueError(T(f'"agents" 의 {cid} role 은 "edit" 또는 "view"', f'The role of {cid} in "agents" must be "edit" or "view"'))
                edit, view = _people(raw, "edit"), _people(raw, "view")
                if not edit and not view:
                    # An empty person list would 403 everyone including the owner — report it as a broken file instead of silently locking
                    raise ValueError(T('사람 목록(edit · view)이 비었다 — 이대로면 주인도 못 들어온다. '
                                       'Access 를 통과한 모두를 들이려면 "edit": ["*"], 아니면 내 이메일을 넣는다. 예: ',
                                       'The people lists (edit, view) are empty - as is, even the owner cannot get in. '
                                       'To admit everyone who passes Access use "edit": ["*"], otherwise add your email. Example: ') + MEMBERS_EXAMPLE)
                self._data = {"edit": edit, "view": view, "agents": {str(k): v for k, v in agents.items()}}
                self.error = self.error_raw = None
            except (OSError, ValueError) as e:
                self._data = {"edit": [], "view": [], "agents": {}, "broken": True}
                self.error_raw = f"{type(e).__name__}: {e}"
                if isinstance(e, json.JSONDecodeError):
                    self.error = T(f"JSON 형식이 틀렸다(줄 {e.lineno}, 칸 {e.colno}) — 쉼표 · 큰따옴표 · 괄호를 확인", f"Invalid JSON (line {e.lineno}, column {e.colno}) - check commas, double quotes and brackets")
                elif isinstance(e, OSError):
                    self.error = T("파일을 읽지 못했다(권한 · 경로를 확인)", "Cannot read the file (check permissions and path)")
                else:
                    self.error = str(e)   # a ValueError raised by this module — already human-readable
                _log(T(f"members.json 을 읽지 못했다 — 아무도 들이지 않는다: {self.error}", f"Cannot read members.json - admitting nobody: {self.error}"))
            return self._data

    @staticmethod
    def _hit(email: str, entries) -> bool:
        for x in entries:
            if x == "*":
                return True
            if x.startswith("@"):
                if email.endswith(x):
                    return True
            elif email == x:
                return True
        return False

    def role_for_email(self, email: str) -> Optional[str]:
        d = self._load()
        if d is None:
            return "edit"
        e = email.strip().lower()
        if self._hit(e, d["edit"]):
            return "edit"
        if self._hit(e, d["view"]):
            return "view"
        return None

    def check(self) -> Optional[str]:
        """Read the file now -> the reason if it is broken (None if missing or valid)."""
        self._load()
        return self.error

    def counts(self) -> dict:
        d = self._load() or {}
        return {k: len(d.get(k) or []) for k in ("edit", "view", "agents")}

    def agent(self, client_id: str) -> Optional[dict]:
        d = self._load()
        if not d:
            return None
        a = d["agents"].get(client_id)
        if not a:
            return None
        role = a.get("role", "edit")
        return {"name": str(a.get("name") or client_id)[:40], "role": role if role in ("edit", "view") else None}


class CfAccess:
    """Auth-chain entry. `build(authn=[CfAccess(team, aud, members_path=...)])`.

    If jwks_url is given, keys are fetched from there (tests, corporate proxy). leeway: slack for exp / nbf (seconds).
    Auth.expires_at = exp + leeway — after that, open WebSockets / SSE are re-asked and closed by the server's periodic check."""

    def __init__(self, team: str, aud, jwks_url: Optional[str] = None, members_path=None, leeway: float = 60.0,
                 clock: Callable[[], float] = time.time, jwks: Optional[Jwks] = None):
        self.team = team_name(team)
        self.aud = [check_aud(a) for a in ([aud] if isinstance(aud, str) else list(aud))]
        if not self.aud:
            raise ValueError(T("Access AUD 태그가 없다", "No Access AUD tag"))
        self.iss = issuer(self.team)
        self.leeway, self.clock = float(leeway), clock
        self.jwks = jwks or Jwks(jwks_url or certs_url(self.team), clock=clock)   # ValueError if not https (except the test exemption)
        self.members = Members(members_path)

    def __repr__(self):
        return f"CfAccess(team={self.team!r}, aud=<{len(self.aud)}>)"

    def warm(self):
        """Prefetch so the first request does not wait (failure is fine — it retries at request time)."""
        threading.Thread(target=self.jwks.refresh, daemon=True).start()

    def verify(self, token: str) -> dict:
        return verify(token, self.jwks.key, self.aud, self.iss, now=self.clock(), leeway=self.leeway)

    def authenticate(self, req):
        Auth = _security().Auth
        if req.is_local_host and any(req.headers.get(h) for h in EDGE_HEADERS):
            # The Host is local but the request came through the Cloudflare edge (a misconfiguration where the front end rewrote Host to localhost) — treat it as an outside request.
            # If a process on the same machine adds this header, it only reduces its own privileges.
            return Auth(deny=True)
        if req.is_local_host or not req.public:
            return None   # inside this machine (invite-key rules) / (if any) a Host added over http follows the existing rules
        token = req.headers.get(HEADER)
        if not token:
            return Auth(deny=True)
        try:
            p = self.verify(token.strip())
        except JwtError:
            return Auth(deny=True)
        until = float(p["exp"]) + self.leeway
        email = p.get("email")
        if isinstance(email, str) and "@" in email:
            e = email.strip().lower()
            return Auth(self.members.role_for_email(e), {"id": e, "name": e.split("@")[0][:40] or e[:40]}, expires_at=until)
        cid = p.get("common_name")
        if isinstance(cid, str) and cid:
            a = self.members.agent(cid)
            if not a:
                return Auth(None, {"id": "service:" + cid[:80], "name": "service"}, expires_at=until)   # service token not in the list -> 403
            return Auth(a["role"], {"id": "agent:" + cid[:80], "name": a["name"], "agent": True}, expires_at=until)
        return Auth(deny=True)


def refuse_with_trust_header(trust_header) -> None:
    """Refuse to start if Access and --trust-header are both enabled (header trust can be forged — only the JWT is trusted)."""
    if trust_header:
        raise SystemExit(T("Cloudflare Access 모드에서는 --trust-header 를 쓸 수 없다 — 서버가 JWT 를 직접 검증한다", "--trust-header cannot be used in Cloudflare Access mode - the server verifies the JWT itself"))
