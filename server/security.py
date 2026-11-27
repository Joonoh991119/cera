# SPDX-License-Identifier: MIT
"""Cera security floor (standard library only) — Host allowlist, Origin check, auth-chain types.

Host allowlist: blocks DNS rebinding. Even if an attacker's domain re-resolves to 127.0.0.1, the request's Host is
still the attacker's domain, which is not in the list -> 421. Each entry is HostRule(host[:port], public). Only requests
that arrive with a public Host are treated as https (Secure, __Host- cookies, HSTS). The X-Forwarded-Proto and
Cf-Visitor headers are ignored (they can be forged from the same machine).

Auth chain (authn): entries that the server's role()/user() consults, in order, before the invite key. Each entry has
`authenticate(req: Req) -> None | Auth`. None means try the next entry (if all return None, fall back to invite key /
cookie); Auth(deny=True) means 401 and no fallback to key / cookie. Agent tokens and Cloudflare Access plug in here.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import re
import socket
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Iterable, List, Optional


def T(ko, en):
    """Korean or English text (server/i18n.py: CERA_LANG > `cera lang` > locale > English)."""
    key = "cera_i18n"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().parent / "i18n.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key].T(ko, en)

LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_HOSTCHARS = re.compile(r"^[a-z0-9._\-\[\]:*]+$")   # '_' is rare in DNS names but does occur in practice


def split_host(value: str):
    """'Host' header (or 'host[:port]') -> (host, port). Lowercased, trailing dot removed. (None, None) if malformed."""
    v = (value or "").strip().lower()
    if not v or len(v) > 260 or not _HOSTCHARS.match(v):
        return None, None
    if v.startswith("["):   # [::1]:8790
        end = v.find("]")
        if end < 0:
            return None, None
        host, rest = v[:end + 1], v[end + 1:]
        if rest and not rest.startswith(":"):
            return None, None
        port = rest[1:]
    elif v.count(":") == 1:
        host, port = v.split(":")
    elif ":" in v:
        return None, None   # an IPv6 address without brackets never appears as a Host header
    else:
        host, port = v, ""
    if port and (not port.isdigit() or not 0 < int(port) < 65536):
        return None, None
    host = host.rstrip(".")
    return (host or None), (str(int(port)) if port else "")


def is_loopback(host: str) -> bool:
    return host in LOOPBACK


def is_loopback_ip(addr: str) -> bool:
    """Whether the socket peer address is on this machine (127.0.0.0/8, ::1, ::ffff:127.x)."""
    try:
        ip = ipaddress.ip_address((addr or "").split("%")[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(ip.is_loopback or (mapped is not None and mapped.is_loopback))


class HostRule:
    """One allowed Host. host is 'name', 'name:port', '[::1]:port', or '*.trycloudflare.com' (one-level wildcard).
    A rule without a port matches a Host without a port and the default port (443 if public, else 80)."""

    __slots__ = ("host", "port", "public", "wild", "any_ip")
    ANY_IP = "<any-ip>"

    @classmethod
    def any_ip_rule(cls, port: int) -> "HostRule":
        """Any IP-literal Host + this port (http). For binding to 0.0.0.0 — works with multiple interfaces or a changing IP.
        DNS rebinding is an attack where an attacker's *name* arrives as Host, so accepting IP literals does not reopen it."""
        r = cls.__new__(cls)
        r.host, r.port, r.public, r.wild, r.any_ip = cls.ANY_IP, str(int(port)), False, False, True
        return r

    def __init__(self, host: str, public: bool = False):
        h, p = split_host(host)
        if not h:
            raise ValueError(T(f"Host 규칙을 읽지 못했다: {host!r}", f"Cannot read the Host rule: {host!r}"))
        self.wild = h.startswith("*.")
        if "*" in (h[2:] if self.wild else h):
            raise ValueError(T(f"와일드카드는 맨 앞 한 단계만: {host!r}", f"A wildcard is allowed only as the first label: {host!r}"))
        self.host, self.port, self.public, self.any_ip = h, p, bool(public), False

    @classmethod
    def parse(cls, spec: str) -> "HostRule":
        """A user-supplied Host (state.allow_hosts, --allow-host). 'https://h' -> public, 'http://h' -> not public.
        With no prefix, loopback, IP, and '.local' names are treated as http (not public); any other name as https (public)."""
        s = str(spec).strip()
        if "://" in s:
            u = urllib.parse.urlsplit(s)
            if u.scheme not in ("http", "https") or not u.netloc:
                raise ValueError(T(f"Host 를 읽지 못했다: {spec!r}", f"Cannot read the Host: {spec!r}"))
            return cls(u.netloc, public=u.scheme == "https")
        h, _ = split_host(s)
        if not h:
            raise ValueError(T(f"Host 를 읽지 못했다: {spec!r}", f"Cannot read the Host: {spec!r}"))
        return cls(s, public=not (is_loopback(h) or _is_ip(h) or h.endswith(".local")))

    def matches(self, host: str, port: str) -> bool:
        if self.any_ip:
            return port == self.port and _is_ip(host)
        if self.port:
            if port != self.port:
                return False
        elif port not in ("", "443" if self.public else "80"):
            return False
        if self.wild:
            suffix = self.host[1:]   # '.trycloudflare.com'
            return host.endswith(suffix) and bool(_LABEL.match(host[:-len(suffix)]))
        return host == self.host

    def __repr__(self):
        return f"HostRule({self.host + (':' + self.port if self.port else '')!r}, public={self.public})"

    def __eq__(self, other):
        return isinstance(other, HostRule) and (self.host, self.port, self.public) == (other.host, other.port, other.public)

    def __hash__(self):
        return hash((self.host, self.port, self.public))


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h.strip("[]"))
        return True
    except ValueError:
        return False


def local_rules(port: int) -> List[HostRule]:
    """Addresses opened on this machine — always allowed (a rebinding attack cannot produce this Host)."""
    return [HostRule(f"{h}:{int(port)}") for h in LOOPBACK]


class HostAllow:
    """Host allowlist. base (local rules added by the server, always present) + the rest, replaced wholesale via set().
    The supervise loop calls set() when it re-reads state.json (the swap is lock-protected, so it is safe to call mid-request)."""

    def __init__(self, rules: Iterable = ()):
        self._lock = threading.Lock()
        self.base: List[HostRule] = []
        self._extra: List[HostRule] = [_rule(r) for r in rules]

    def set_base(self, rules: Iterable) -> None:
        with self._lock:
            self.base = [_rule(r) for r in rules]

    def set(self, rules: Iterable) -> None:
        new = [_rule(r) for r in rules]
        with self._lock:
            self._extra = new

    def add(self, rule) -> None:
        r = _rule(rule)
        with self._lock:
            self._extra = self._extra + [r]

    @property
    def rules(self) -> List[HostRule]:
        with self._lock:
            return list(self.base) + list(self._extra)

    def match(self, host_header: Optional[str]) -> Optional[HostRule]:
        """The matching rule (None if none). Local rules are checked first — a Host in both is treated as local (http)."""
        h, p = split_host(host_header or "")
        if not h:
            return None
        rules = self.rules
        for r in sorted(rules, key=lambda r: r.public):
            if r.matches(h, p):
                return r
        return None

    def origin_ok(self, origin: Optional[str]) -> bool:
        """Whether the browser's Origin (scheme://host[:port]) is an allowed Host and its scheme matches that rule (public=https).
        'null', malformed values, and other schemes are rejected."""
        if not origin or origin == "null":
            return False
        u = urllib.parse.urlsplit(origin.strip())
        if u.scheme not in ("http", "https") or not u.netloc or u.path not in ("", "/"):
            return False
        h, p = split_host(u.netloc)
        if not h:
            return False
        for r in self.rules:
            if r.matches(h, p) and r.public == (u.scheme == "https"):
                return True
        return False


def _rule(r) -> HostRule:
    return r if isinstance(r, HostRule) else HostRule.parse(r)


def local_names() -> List[str]:
    """This machine's '<name>.local' (Bonjour) — for reaching it by name on the same Wi-Fi."""
    out = []
    try:
        n = socket.gethostname().strip().lower().rstrip(".")
    except OSError:
        return out
    if n and n not in ("localhost",):
        out.append(n if n.endswith(".local") else n.split(".")[0] + ".local")
    return out


def lan_rules(port: int) -> List[HostRule]:
    """Rules added when bound to 0.0.0.0 (--lan) — any IP literal with a matching port + '<name>.local:PORT' (http)."""
    rules = [HostRule.any_ip_rule(port)]
    for n in local_names():
        try:
            rules.append(HostRule(f"{n}:{int(port)}"))
        except ValueError:
            pass
    return rules


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def rules_for_state(st: dict, port: int, lan: Optional[str] = None) -> List[HostRule]:
    """state.json -> Host rules. Local set + (--lan) any IP of this machine and <name>.local + public address (public_url, hostname) + allow_hosts.
    A quick tunnel allows '*.trycloudflare.com' until its address appears, then narrows to that one name.
    If the tunnel was given up (error), the wildcard is not reopened. An unreadable entry is dropped on its own (so the supervise loop survives)."""
    rules = local_rules(port)
    if st.get("host") == "0.0.0.0":
        rules += lan_rules(port)
        if lan:
            rules.append(HostRule(f"{lan}:{int(port)}"))

    def add(fn):
        try:
            rules.append(fn())
        except (ValueError, TypeError):
            pass
    pu = st.get("public_url")
    if pu:
        u = urllib.parse.urlsplit(str(pu))
        if u.scheme in ("http", "https") and u.netloc:
            add(lambda: HostRule(u.netloc, public=u.scheme == "https"))
    elif st.get("tunnel") == "cloudflared" and not st.get("error"):
        rules.append(HostRule("*.trycloudflare.com", public=True))
    if st.get("hostname"):
        add(lambda: HostRule(str(st["hostname"]), public=True))
    for h in st.get("allow_hosts") or []:
        add(lambda h=h: HostRule.parse(h))
    return rules


# ------------------------------------------------------------------ auth chain
class Req:
    """The request as seen by a chain entry — headers, Host, whether the Host is local, path, direct peer IP."""

    __slots__ = ("headers", "host", "is_local_host", "path", "client_ip", "public")

    def __init__(self, headers, host: str, is_local_host: bool, path: str, client_ip: str, public: bool = False):
        self.headers, self.host, self.is_local_host = headers, host, is_local_host
        self.path, self.client_ip, self.public = path, client_ip, public


class Auth:
    """A chain entry's answer. role: 'edit' | 'view' | None (authenticated but not authorized -> 403).
    user: {'id', 'name', ('agent': True)} or None (the name follows the existing rules — cookie, X-Tb-Name).
    deny=True: 401 — no fallback to invite key / cookie (e.g. a public Host without a JWT).
    expires_at: last moment the answer is valid — open WebSockets / SSE are re-checked after that (every revalidate_interval in build)."""

    __slots__ = ("role", "user", "deny", "expires_at")

    def __init__(self, role: Optional[str] = None, user: Optional[dict] = None, deny: bool = False,
                 expires_at: Optional[float] = None):
        self.role, self.user, self.deny = role, user, bool(deny)
        self.expires_at = expires_at   # (optional) when this answer ends (epoch seconds) — after that the server asks again (JWT exp, token expiry)

    def __repr__(self):
        return f"Auth(role={self.role!r}, user={self.user!r}, deny={self.deny}, expires_at={self.expires_at!r})"


def trust_header_warning() -> str:
    return T("주의: --trust-header 는 앞단이 넣는 머리를 그대로 믿는다. 같은 맥의 다른 프로세스나 "
             "DNS 리바인딩으로 위조될 수 있다 — 공개 주소에는 Cloudflare Access(JWT 검증)를 쓴다.",
             "Warning: --trust-header trusts the header set by the front proxy as is. Another process on the same Mac or "
             "DNS rebinding can forge it - for a public address, use Cloudflare Access (JWT verification).")
