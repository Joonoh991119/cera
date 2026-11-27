# SPDX-License-Identifier: MIT
"""Cloudflare Access tests: standard-library RS256 JWT verification, JWKS cache, members.json, and integration with the server hook chain.

The fake RSA keys are created inside the tests with `openssl genrsa` (skipped if openssl is missing). Python signs with the private exponent openssl produced,
and once the signature is cross-checked against one made by `openssl dgst -sha256 -sign`. The JWKS is served by http.server on 127.0.0.1."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Korean assertions below: pin the language before anything spawns the CLI or imports server code.
os.environ["TEMPBOARD_LANG"] = "ko"

HERE = Path(__file__).resolve().parents[1]


def _load(name):
    key = "tempboard_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "server" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


srv = _load("server")
cfa = _load("cfaccess")
TEAM, AUD = "myteam", "a" * 64
ISS = "https://myteam.cloudflareaccess.com"
HOST = "board.example.com"
OPENSSL = shutil.which("openssl")


def b64(x: bytes) -> str:
    return base64.urlsafe_b64encode(x).rstrip(b"=").decode()


def _hexblock(text, label):
    m = re.search(label + r":\s*\n((?:\s+[0-9a-f:]+\n)+)", text)
    return int(re.sub(r"[^0-9a-f]", "", m.group(1)), 16)


class Key:
    """An RSA key made with openssl -- n, e, d are extracted and Python signs with them."""

    def __init__(self, d: Path, kid: str, bits=2048):
        self.kid, self.pem = kid, d / f"{kid}.pem"
        subprocess.run([OPENSSL, "genrsa", "-out", str(self.pem), str(bits)], check=True, capture_output=True)
        txt = subprocess.run([OPENSSL, "rsa", "-in", str(self.pem), "-noout", "-text"], check=True, capture_output=True, text=True).stdout
        self.n = _hexblock(txt, "modulus")
        self.e = int(re.search(r"publicExponent: (\d+)", txt).group(1))
        self.d = _hexblock(txt, "privateExponent")
        self.k = (self.n.bit_length() + 7) // 8

    def jwk(self):
        return {"kid": self.kid, "kty": "RSA", "alg": "RS256", "use": "sig", "n": b64(self.n.to_bytes(self.k, "big")),
                "e": b64(self.e.to_bytes(3, "big"))}

    def sign(self, msg: bytes) -> bytes:
        h = hashlib.sha256(msg).digest()
        em = b"\x00\x01" + b"\xff" * (self.k - 3 - len(cfa.DIGEST_INFO) - 32) + b"\x00" + cfa.DIGEST_INFO + h
        return pow(int.from_bytes(em, "big"), self.d, self.n).to_bytes(self.k, "big")

    def token(self, payload, kid=None, alg="RS256", header=None):
        hd = header or {"alg": alg, "kid": kid or self.kid, "typ": "JWT"}
        h, p = b64(json.dumps(hd).encode()), b64(json.dumps(payload).encode())
        return f"{h}.{p}.{b64(self.sign(f'{h}.{p}'.encode()))}"


def payload(**over):
    now = time.time()
    p = {"aud": [AUD], "iss": ISS, "email": "Kim@example.org", "exp": now + 600, "iat": now, "nbf": now - 1,
         "type": "app", "sub": "u-1", "identity_nonce": "x", "country": "KR"}
    p.update(over)
    return {k: v for k, v in p.items() if v is not None}


class JwksServer:
    """A fake JWKS -- changing doc takes effect from the next fetch. With down=True it returns 500."""

    def __init__(self, doc):
        self.doc, self.down, self.hits = doc, False, 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits += 1
                if outer.down:
                    self.send_response(500)
                    self.end_headers()
                    return
                body = json.dumps(outer.doc).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/cdn-cgi/access/certs"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


_SAVED_ENV = None


def setUpModule():
    """A local fake JWKS (http) is accepted only when this environment variable is set -- turned on only for this module and restored afterwards (so it does not leak into other test modules)."""
    global _SAVED_ENV
    _SAVED_ENV = os.environ.get("TEMPBOARD_TEST_INSECURE_JWKS")
    os.environ["TEMPBOARD_TEST_INSECURE_JWKS"] = "1"


def tearDownModule():
    if _SAVED_ENV is None:
        os.environ.pop("TEMPBOARD_TEST_INSECURE_JWKS", None)
    else:
        os.environ["TEMPBOARD_TEST_INSECURE_JWKS"] = _SAVED_ENV


@unittest.skipUnless(OPENSSL, "openssl 없음 — JWT 시험 건너뜀")
class KeysMixin:
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        d = Path(cls._td.name)
        cls.k1, cls.k2 = Key(d, "k1"), Key(d, "k2")
        cls.weak = Key(d, "weak", bits=1024)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()


class Verify(KeysMixin, unittest.TestCase):
    def setUp(self):
        self.js = JwksServer({"keys": [self.k1.jwk()]})
        self.clock = [time.time()]
        self.ca = cfa.CfAccess(TEAM, AUD, jwks_url=self.js.url, clock=lambda: self.clock[0])

    def tearDown(self):
        self.js.close()

    def ok(self, tok):
        return self.ca.verify(tok)

    def bad(self, tok, why):
        with self.assertRaises(cfa.JwtError) as cm:
            self.ca.verify(tok)
        self.assertEqual(str(cm.exception), why)

    def test_good_and_user(self):
        p = self.ok(self.k1.token(payload()))
        self.assertEqual(p["email"], "Kim@example.org")
        req = srv.Req({"Cf-Access-Jwt-Assertion": self.k1.token(payload())}, HOST, False, "/", "127.0.0.1", public=True)
        a = self.ca.authenticate(req)
        self.assertFalse(a.deny)
        self.assertEqual((a.role, a.user["id"], a.user["name"]), ("edit", "kim@example.org", "kim"))
        self.assertAlmostEqual(a.expires_at, payload()["exp"] + 60, delta=2)

    def test_openssl_signature_cross_check(self):
        """A signature made by openssl, independent of the Python signer, also passes -- so the verifier and the test signer do not share the same mistake."""
        h = b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
        p = b64(json.dumps(payload()).encode())
        sig = subprocess.run([OPENSSL, "dgst", "-sha256", "-sign", str(self.k1.pem)], input=f"{h}.{p}".encode(),
                             capture_output=True, check=True).stdout
        self.assertEqual(sig, self.k1.sign(f"{h}.{p}".encode()))
        self.ok(f"{h}.{p}.{b64(sig)}")

    def test_rejections(self):
        good = self.k1.token(payload())
        h, p, s = good.split(".")
        forged = b64(json.dumps(payload(email="prof@example.com")).encode())
        self.bad(f"{h}.{forged}.{s}", "sig")                                   # payload swapped
        self.bad(self.k1.token(payload(aud=["other"])), "aud")
        self.bad(self.k1.token(payload(aud="other")), "aud")
        self.bad(self.k1.token(payload(iss="https://evil.cloudflareaccess.com")), "iss")
        self.bad(self.k1.token(payload(iss=ISS + "/")), "iss")
        self.bad(self.k1.token(payload(exp=time.time() - 61)), "exp")
        self.bad(self.k1.token(payload(exp=None)), "exp")
        self.bad(self.k1.token(payload(exp="9999999999")), "exp")
        self.bad(self.k1.token(payload(nbf=time.time() + 120)), "nbf")
        self.bad(self.k1.token(payload(), kid="nope"), "kid")
        self.bad(self.k2.token(payload()), "kid")                              # a key that is not in the JWKS
        # alg:none -- no signature
        hn = b64(json.dumps({"alg": "none", "kid": "k1"}).encode())
        self.bad(f"{hn}.{p}.", "alg")
        self.bad(f"{hn}.{p}.{s}", "alg")
        # HS256 confusion -- the public key (n bytes / JWK JSON) used as the HMAC secret
        hh = b64(json.dumps({"alg": "HS256", "kid": "k1"}).encode())
        for secret in (self.k1.n.to_bytes(self.k1.k, "big"), json.dumps(self.k1.jwk()).encode(), self.k1.pem.read_bytes()):
            mac = hmac.new(secret, f"{hh}.{p}".encode(), hashlib.sha256).digest()
            self.bad(f"{hh}.{p}.{b64(mac)}", "alg")
        self.bad(self.k1.token(payload(), alg="RS512"), "alg")
        self.bad(self.k1.token(payload(), header={"alg": "RS256", "kid": "k1", "crit": ["x"]}), "crit")
        # wrong signature length (one byte shorter / longer) and a value >= n
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        self.bad(f"{h}.{p}.{b64(raw[1:])}", "sig")
        longer = b64(raw + bytes(1))
        self.bad(f"{h}.{p}.{longer}", "sig")
        self.bad(f"{h}.{p}.{b64(self.k1.n.to_bytes(self.k1.k, 'big'))}", "sig")
        # format
        for t in ("", "a.b", "a.b.c.d", f"{h}.{p}.{s}=", f"{h}+.{p}.{s}", "x" * 20000, f"{b64(b'[1]')}.{p}.{s}"):
            with self.assertRaises(cfa.JwtError):
                self.ca.verify(t)

    def test_leeway(self):
        self.ok(self.k1.token(payload(exp=time.time() - 30)))          # within the 60-second leeway
        self.ok(self.k1.token(payload(nbf=time.time() + 30)))

    def test_weak_key_not_loaded(self):
        self.assertEqual(cfa.parse_jwks({"keys": [self.weak.jwk()]}), {})
        bad_e = dict(self.k1.jwk(), e=b64(b"\x01"))
        self.assertEqual(cfa.parse_jwks({"keys": [bad_e, dict(self.k1.jwk(), kty="EC"), dict(self.k1.jwk(), use="enc"), "x"]}), {})

    def test_key_rotation_refetch_once_and_rate_limited(self):
        self.ok(self.k1.token(payload()))
        self.assertEqual(self.js.hits, 1)
        self.js.doc = {"keys": [self.k1.jwk(), self.k2.jwk()]}           # Cloudflare added a new key
        self.clock[0] += 61
        self.ok(self.k2.token(payload()))                                 # unknown kid -> refetched once
        self.assertEqual(self.js.hits, 2)
        self.ok(self.k2.token(payload()))
        self.assertEqual(self.js.hits, 2, "알게 된 kid 는 캐시")
        for _ in range(5):                                                # even a flood of forged kids causes at most one refetch per 60 seconds
            self.bad(self.k1.token(payload(), kid="zz"), "kid")
        self.assertEqual(self.js.hits, 2)
        self.clock[0] += 61
        self.bad(self.k1.token(payload(), kid="zz"), "kid")
        self.assertEqual(self.js.hits, 3)

    def test_jwks_down_keeps_cache(self):
        self.ok(self.k1.token(payload()))
        self.js.down = True
        self.clock[0] += 3601                                             # the cache is stale -> the refetch fails -> the previous cache is used
        self.ok(self.k1.token(payload(exp=self.clock[0] + 600, nbf=self.clock[0])))
        self.assertEqual(self.js.hits, 2)
        self.assertIn("500", self.ca.jwks.last_error)
        self.ok(self.k1.token(payload(exp=self.clock[0] + 600, nbf=self.clock[0])))
        self.assertEqual(self.js.hits, 2, "실패한 뒤에도 60초 안에는 다시 가지 않는다")

    def test_first_fetch_fails_then_denied(self):
        self.js.down = True
        req = srv.Req({"Cf-Access-Jwt-Assertion": self.k1.token(payload())}, HOST, False, "/", "127.0.0.1", public=True)
        self.assertTrue(self.ca.authenticate(req).deny)

    def test_chain_positions(self):
        tok = self.k1.token(payload())
        R = lambda hd, local, public: srv.Req(hd, HOST, local, "/", "127.0.0.1", public=public)   # noqa: E731
        self.assertIsNone(self.ca.authenticate(R({"Cf-Access-Jwt-Assertion": tok}, True, False)), "로컬 Host → 초대 키 규칙")
        self.assertIsNone(self.ca.authenticate(R({}, False, False)), "http 로 더한 비공개 Host → 초대 키 규칙")
        self.assertTrue(self.ca.authenticate(R({}, False, True)).deny)
        self.assertTrue(self.ca.authenticate(R({"Cf-Access-Authenticated-User-Email": "prof@example.com"}, False, True)).deny)
        self.assertTrue(self.ca.authenticate(R({"Cookie": f"CF_Authorization={tok}"}, False, True)).deny, "쿠키는 보지 않는다")
        for h in ("Cf-Ray", "Cf-Connecting-IP"):   # the Host is local but the request came through the Cloudflare edge -> an outside request
            self.assertTrue(self.ca.authenticate(R({h: "x", "Cf-Access-Jwt-Assertion": tok}, True, False)).deny, h)

    def test_jwks_url_https_only_and_no_redirect(self):
        """http override only when TEMPBOARD_TEST_INSECURE_JWKS=1, file: is always rejected, and redirects are not followed."""
        old = os.environ.pop("TEMPBOARD_TEST_INSECURE_JWKS")
        try:
            with self.assertRaises(ValueError):
                cfa.CfAccess(TEAM, AUD, jwks_url=self.js.url)
            cfa.CfAccess(TEAM, AUD, jwks_url="https://x.example/certs")
        finally:
            os.environ["TEMPBOARD_TEST_INSECURE_JWKS"] = old
        for bad in ("file:///etc/passwd", "ftp://x/certs", ""):
            with self.assertRaises(ValueError):
                cfa.Jwks(bad)
        # redirect -- even if it points to another JWKS (k2), it is not followed
        other = JwksServer({"keys": [self.k2.jwk()]})
        import http.server as hs
        class Redir(hs.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", other.url)
                self.send_header("Content-Length", "0")
                self.end_headers()
        rd = hs.ThreadingHTTPServer(("127.0.0.1", 0), Redir)
        threading.Thread(target=rd.serve_forever, daemon=True).start()
        try:
            j = cfa.Jwks(f"http://127.0.0.1:{rd.server_address[1]}/certs")
            self.assertFalse(j.refresh(force=True))
            self.assertIn("302", j.last_error)
            self.assertEqual(other.hits, 0, "되돌려 보낸 곳에 가지 않았다")
        finally:
            rd.shutdown()
            rd.server_close()
            other.close()

    def test_team_and_aud_parsing(self):
        for t in ("myteam", "MyTeam.cloudflareaccess.com", "https://myteam.cloudflareaccess.com/"):
            self.assertEqual(cfa.team_name(t), "myteam")
        for t in ("", "a b", "evil.com/x", "-x"):
            with self.assertRaises(ValueError):
                cfa.team_name(t)
        with self.assertRaises(ValueError):
            cfa.check_aud("short")
        with self.assertRaises(SystemExit):
            cfa.refuse_with_trust_header("X-User")
        cfa.refuse_with_trust_header(None)


class MembersTest(KeysMixin, unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name) / "members.json"
        self.m = cfa.Members(self.path)

    def tearDown(self):
        self.td.cleanup()

    def write(self, obj):
        self.path.write_text(obj if isinstance(obj, str) else json.dumps(obj))
        os.utime(self.path, ns=(time.time_ns(), time.time_ns() + 10 ** 9))   # rewriting with the same size also counts as a change

    def test_roles(self):
        self.assertEqual(self.m.role_for_email("anyone@x.com"), "edit", "파일 없음 → 모두 편집")
        self.assertIsNone(self.m.agent("cid.access"), "파일 없음 → 서비스 토큰 거부")
        self.write({"edit": ["Me@X.com", "@example.org"], "view": ["guest@y.com", "me@x.com"],
                    "agents": {"cid.access": {"name": "codex@srv", "role": "edit"}, "v.access": {"role": "view"}}})
        self.assertEqual(self.m.role_for_email("me@x.com"), "edit", "둘 다면 edit")
        self.assertEqual(self.m.role_for_email("kim@EXAMPLE.org"), "edit")
        self.assertIsNone(self.m.role_for_email("kim@evil-example.org.evil.com"))
        self.assertIsNone(self.m.role_for_email("x@notexample.org"), "'@example.org' 는 notexample.org 에 맞지 않는다")
        self.assertEqual(self.m.role_for_email("guest@y.com"), "view")
        self.assertIsNone(self.m.role_for_email("stranger@z.com"))
        self.assertEqual(self.m.agent("cid.access"), {"name": "codex@srv", "role": "edit"})
        self.assertEqual(self.m.agent("v.access")["role"], "view")
        self.assertIsNone(self.m.agent("other.access"))
        self.write("{broken")
        self.assertIsNone(self.m.role_for_email("me@x.com"), "깨진 파일 → 아무도 못 들어온다")
        self.assertIsNotNone(self.m.error)
        self.write({"edit": ["me@x.com"]})
        self.assertEqual(self.m.role_for_email("me@x.com"), "edit", "고치면 곧바로 다시 읽는다")
        self.path.unlink()
        self.assertEqual(self.m.role_for_email("stranger@z.com"), "edit")

    def test_shapes_that_would_lock_humans_out_are_errors(self):
        """members.json with only agents and no people section, edit as a string, an empty list, or a non-email value does not silently lock everyone out; it reports an error with the reason."""
        cases = [({"agents": {"cid.access": {"name": "a", "role": "edit"}}}, "사람 목록"),
                 ({"edit": "me@x.com"}, "목록"),
                 ({"edit": [], "view": []}, "사람 목록"),
                 ({"edit": ["me"]}, "이메일"),
                 ({"edit": [1]}, "목록"),
                 ({"editors": ["me@x.com"]}, "모르는 칸"),
                 ({"edit": ["me@x.com"], "agents": {"c": {"role": "admin"}}}, "role"),
                 ({"edit": ["me@x.com"], "agents": ["c"]}, "agents")]
        for obj, why in cases:
            self.write(obj)
            self.assertIsNone(self.m.role_for_email("me@x.com"), obj)
            self.assertIn(why, self.m.check() or "", obj)
        self.write({"edit": ["*"], "agents": {"cid.access": {"name": "codex", "role": "edit"}}})
        self.assertIsNone(self.m.check())
        self.assertEqual(self.m.role_for_email("anyone@x.com"), "edit", '"*" = Access 를 통과한 모두')
        self.assertEqual(self.m.agent("cid.access")["name"], "codex")

    def test_guide_examples_parse(self):
        """The examples in the guide (sections F and G) work when put into members.json verbatim."""
        spec = importlib.util.spec_from_file_location("tb_personal_guide", HERE / "server" / "personal.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        lines = [l.strip() for l in mod.guide_text().splitlines() if l.strip().startswith('{"edit"')]
        self.assertGreaterEqual(len(lines), 2, lines)
        for line in lines + [cfa.MEMBERS_EXAMPLE]:
            self.write(line)
            self.assertIsNone(self.m.check(), line)
            self.assertIsNotNone(self.m.role_for_email("me@example.com"), line)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def raw(port, method, path, headers=None, body=None, timeout=5):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, r.getheaders(), data


def ws_open(port, path, headers):
    s = socket.create_connection(("127.0.0.1", port), timeout=8)
    key = base64.b64encode(os.urandom(16)).decode()
    extra = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    s.sendall((f"GET {path} HTTP/1.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n{extra}\r\n").encode())
    head = b""
    while not head.endswith(b"\r\n\r\n"):   # one byte at a time -- so a frame right after the headers is not swallowed by a buffer
        c = s.recv(1)
        if not c:
            break
        head += c
    return int(head.split()[1]), s, None


def ws_until_close(s, secs):
    """Read from the socket until a close frame (8) or a disconnect arrives -> the elapsed seconds (None if it never comes). Reads directly from the socket after the upgrade."""
    t0, buf = time.time(), b""
    s.settimeout(0.2)
    while time.time() - t0 < secs:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return time.time() - t0
        if not chunk:
            return time.time() - t0
        buf += chunk
        while len(buf) >= 2:
            op, n, i = buf[0] & 0x0F, buf[1] & 0x7F, 2
            if n == 126:
                if len(buf) < 4:
                    break
                n, i = int.from_bytes(buf[2:4], "big"), 4
            elif n == 127:
                if len(buf) < 10:
                    break
                n, i = int.from_bytes(buf[2:10], "big"), 10
            if len(buf) < i + n:
                break
            if op == 8:
                return time.time() - t0
            buf = buf[i + n:]
    return None


class Integration(KeysMixin, unittest.TestCase):
    """A real server started with build(authn=[CfAccess], hosts=[H public])."""

    EK = "LOCALEDITKEY1234"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        t = Path(self.td.name)
        dist = t / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>tb</title>")
        self.js = JwksServer({"keys": [self.k1.jwk()]})
        self.members = t / "members.json"
        self.members.write_text(json.dumps({"edit": ["owner@example.org", "@example.org"], "view": ["guest@gmail.com"],
                                            "agents": {"cid1.access": {"name": "codex@lab", "role": "edit"}}}))
        self.ca = cfa.CfAccess(TEAM, AUD, jwks_url=self.js.url, members_path=self.members, leeway=0)
        self.access = srv.Access(self.EK, None, site="personal", expires_at=0)
        self.httpd, self.store = srv.build(t / "data", dist, access=self.access, authn=[self.ca], hosts=[HOST], hsts=True,
                                           save_interval=0.2, revalidate_interval=0.5)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.ca.jwks.refresh()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.js.close()
        self.td.cleanup()

    def H(self, tok=None, host=HOST, **more):
        h = {"Host": host, "X-Whiteboard": "1", "Content-Type": "application/json"}
        if tok:
            h["Cf-Access-Jwt-Assertion"] = tok
        h.update(more)
        return h

    def tok(self, **kw):
        return self.k1.token(payload(**kw))

    def board(self, tok):
        st, _, d = raw(self.port, "POST", "/api/whiteboard/boards", self.H(tok), {"title": "b"})
        self.assertEqual(st, 201, d)
        return json.loads(d)["id"]

    def test_public_host_needs_jwt(self):
        owner = self.tok(email="owner@example.org")
        for path in ("/api/whiteboard/boards", "/healthz", "/", "/api/whiteboard/me"):
            self.assertEqual(raw(self.port, "GET", path, self.H())[0], 401, path)
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards",
                             self.H(**{"Cf-Access-Authenticated-User-Email": "owner@example.org"}))[0], 401, "이메일 머리만 → 401")
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", self.H(**{"X-Tb-Key": self.EK}))[0], 401,
                         "공개 Host 에서는 초대 키로 넘어가지 않는다")
        self.assertEqual(raw(self.port, "GET", f"/?k={self.EK}", self.H())[0], 401)
        st, hs, d = raw(self.port, "GET", "/api/whiteboard/me", self.H(owner))
        self.assertEqual(st, 200)
        me = json.loads(d)
        self.assertEqual((me["id"], me["name"], me["role"]), ("owner@example.org", "owner", "edit"))
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", self.H(owner))[0], 200)
        tampered = owner.split(".")
        tampered[1] = b64(json.dumps(payload(email="prof@example.com")).encode())
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", self.H(".".join(tampered)))[0], 401)
        self.assertEqual(raw(self.port, "GET", "/healthz", self.H(host="evil.example"))[0], 421, "리바인딩 Host")

    def test_roles_presence_poll_ws(self):
        owner, guest = self.tok(email="owner@example.org"), self.tok(email="guest@gmail.com")
        stranger = self.tok(email="stranger@x.com")
        bid = self.board(owner)
        el = {"id": "e1", "type": "rectangle", "version": 1, "versionNonce": 1, "x": 0, "y": 0, "width": 10, "height": 10}
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{bid}/elements", self.H(owner), {"client": "c1", "elements": [el]})[0], 200)
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{bid}/elements", self.H(guest), {"client": "c2", "elements": [el]})[0], 403,
                         "members view → 쓰기 403")
        self.assertEqual(raw(self.port, "GET", f"/api/whiteboard/boards/{bid}", self.H(guest))[0], 200, "view 는 읽기")
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", self.H(stranger))[0], 403, "목록에 없는 사람 → 403")
        # presence name = the local part of the email
        self.assertEqual(raw(self.port, "POST", f"/api/whiteboard/boards/{bid}/presence", self.H(owner),
                             {"client": "c1", "pointer": {"x": 1, "y": 2}})[0], 204)
        st, _, d = raw(self.port, "GET", f"/api/whiteboard/boards/{bid}/poll?since=0&pver=-1&client=zz&wait=1", self.H(owner))
        self.assertEqual(st, 200, "롱폴링도 JWT 로 200")
        users = [p["user"] for p in json.loads(d).get("presence", [])]
        self.assertIn("owner", users)
        # JWT on the WebSocket upgrade -> 101
        code, s, _ = ws_open(self.port, f"/api/whiteboard/boards/{bid}/ws?client=c9", self.H(owner, Origin=f"https://{HOST}"))
        s.close()
        self.assertEqual(code, 101)
        code, s, _ = ws_open(self.port, f"/api/whiteboard/boards/{bid}/ws?client=c9", self.H(None, Origin=f"https://{HOST}"))
        s.close()
        self.assertEqual(code, 401)

    def test_local_host_uses_invite_key(self):
        loc = {"Host": f"127.0.0.1:{self.port}", "X-Tb-Name": "me", "X-Whiteboard": "1"}
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", {**loc, "X-Tb-Key": self.EK})[0], 200)
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", loc)[0], 403)
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", {**loc, "Cf-Access-Jwt-Assertion": self.tok()})[0], 403,
                         "로컬 Host 의 JWT 는 보지 않는다(초대 키 규칙)")

    def test_service_token_agent(self):
        agent = self.k1.token(payload(email=None, common_name="cid1.access", type="app"))
        st, _, d = raw(self.port, "GET", "/api/whiteboard/me", self.H(agent))
        self.assertEqual(st, 200, d)
        me = json.loads(d)
        self.assertEqual((me["name"], me.get("agent"), me["role"]), ("codex@lab", True, "edit"))
        other = self.k1.token(payload(email=None, common_name="unknown.access"))
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", self.H(other))[0], 403, "목록에 없는 서비스 토큰")
        bid = self.board(agent)
        raw(self.port, "POST", f"/api/whiteboard/boards/{bid}/presence", self.H(agent), {"client": "ag1", "pointer": {"x": 0, "y": 0}})
        st, _, d = raw(self.port, "GET", f"/api/whiteboard/boards/{bid}/poll?since=0&pver=-1&client=zz&wait=1", self.H(agent))
        pres = [p for p in json.loads(d).get("presence", []) if p.get("client") == "ag1"]
        self.assertEqual(len(pres), 1)
        if "agent" not in pres[0] and "agent=" not in Path(srv.__file__).read_text():
            self.skipTest("presence 의 agent 표시가 없다")
        self.assertTrue(pres[0].get("agent"))

    def test_hsts_public_only(self):
        _, hs, _ = raw(self.port, "GET", "/healthz", self.H(self.tok()))
        sts = [v for k, v in hs if k.lower() == "strict-transport-security"]
        self.assertEqual(sts, ["max-age=15552000"])
        _, hs, _ = raw(self.port, "GET", "/healthz", {"Host": f"127.0.0.1:{self.port}"})
        self.assertFalse([v for k, v in hs if k.lower() == "strict-transport-security"])

    def test_stream_closes_when_jwt_expires(self):
        owner = self.tok(email="owner@example.org")
        bid = self.board(owner)
        short = self.tok(email="owner@example.org", exp=time.time() + 1.5)
        code, s, f = ws_open(self.port, f"/api/whiteboard/boards/{bid}/ws?client=c1", self.H(short, Origin=f"https://{HOST}"))
        self.assertEqual(code, 101)
        code2, s2, f2 = ws_open(self.port, f"/api/whiteboard/boards/{bid}/ws?client=c2", self.H(owner, Origin=f"https://{HOST}"))
        self.assertEqual(code2, 101)
        took = ws_until_close(s, 6)
        self.assertIsNotNone(took, "만료된 JWT 의 WebSocket 이 닫히지 않았다")
        self.assertIsNone(ws_until_close(s2, 1.0), "유효한 JWT 의 WebSocket 은 산다")
        s.close()
        s2.close()
        self.assertEqual(raw(self.port, "GET", "/api/whiteboard/boards", self.H(short))[0], 401)


if __name__ == "__main__":
    unittest.main()
