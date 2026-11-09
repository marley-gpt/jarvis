import array
import hashlib
import io
import math
import random
import socket
import struct
import threading
import time
import wave

from helpers import BrainTest
from test_call import FAKE_STT, FakeDashboard

from jarvis import call, paths, sip

PW = "phone-secret"


def tone(seconds=0.4, rate=24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array.array("h", (int(9000 * math.sin(i / 8)) for i in range(int(seconds * rate)))).tobytes())
    return buf.getvalue()


class FakePhone:
    """A softphone: registers, answers or turns down JARVIS's calls, rings him, sends and hears RTP."""

    def __init__(self, server_addr, password=PW):
        self.sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip.bind(("127.0.0.1", 0))
        self.sip.settimeout(5)
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind(("127.0.0.1", 0))
        self.rtp.settimeout(0.2)
        self.server, self.password = server_addr, password
        self.port, self.rtp_port = self.sip.getsockname()[1], self.rtp.getsockname()[1]
        self.heard = []
        self.listening = True
        threading.Thread(target=self._hear, daemon=True).start()

    def close(self):
        self.listening = False
        self.sip.close()
        self.rtp.close()

    def _hear(self):
        while self.listening:
            try:
                data, _ = self.rtp.recvfrom(2048)
                self.heard.append(data[12:])
            except (socket.timeout, OSError):
                continue

    def loud_frames_heard(self):
        return sum(1 for f in self.heard if sip.rms(sip.ulaw_to_pcm(f)) > 0.05)

    def send(self, text):
        self.sip.sendto(text.replace("\n", "\r\n").encode(), self.server)

    def recv(self, want=None):
        while True:
            msg = sip.Msg.parse(self.sip.recvfrom(65535)[0])
            if want is None or msg.first.startswith(want) or (not msg.is_request and str(msg.status) == want):
                return msg

    def request(self, method, cid, cseq, extra="", body="", auth=""):
        via = f"SIP/2.0/UDP 127.0.0.1:{self.port};branch=z9hG4bK{random.getrandbits(32)};rport"
        lines = [f"{method} sip:{sip.REALM}@127.0.0.1 SIP/2.0", f"Via: {via}", "Max-Forwards: 70",
                 f"From: <sip:owner@127.0.0.1>;tag=ph1", "To: <sip:owner@127.0.0.1>", f"Call-ID: {cid}",
                 f"CSeq: {cseq} {method}", f"Contact: <sip:owner@127.0.0.1:{self.port}>;expires=600"]
        if auth:
            lines.append(auth)
        if extra:
            lines.append(extra)
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        self.sip.sendto(("\r\n".join(lines) + "\r\n\r\n" + body).encode(), self.server)

    def authorization(self, challenge, method, password=None):
        p = sip.auth_params(challenge.get("www-authenticate")[6:])
        uri = f"sip:{sip.REALM}@127.0.0.1"
        resp = sip.digest("owner", password or self.password, method, uri, p["nonce"], "00000001", "abc", "auth")
        return (f'Authorization: Digest username="owner", realm="{sip.REALM}", nonce="{p["nonce"]}", uri="{uri}", '
                f'response="{resp}", algorithm=MD5, qop=auth, nc=00000001, cnonce="abc"')

    def register(self, password=None):
        self.request("REGISTER", "reg-1", 1)
        challenge = self.recv()
        assert challenge.status == 401, challenge.first
        self.request("REGISTER", "reg-1", 2, auth=self.authorization(challenge, "REGISTER", password))
        return self.recv()

    def sdp(self):
        return sip.sdp("127.0.0.1", self.rtp_port)

    def reply(self, req, code, reason, body=""):
        lines = [f"SIP/2.0 {code} {reason}"] + [f"Via: {v}" for v in req.all("via")] + [
            f"From: {req.get('from')}", f"To: {req.get('to')};tag=ph2", f"Call-ID: {req.get('call-id')}",
            f"CSeq: {req.get('cseq')}", f"Contact: <sip:owner@127.0.0.1:{self.port}>"]
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        self.sip.sendto(("\r\n".join(lines) + "\r\n\r\n" + body).encode(), self.server)

    def talk(self, dest, seconds_loud=1.2, seconds_quiet=0.9):
        """Speak (loud noise, as far as voice detection goes), then go quiet so JARVIS takes his turn."""
        seq = 0
        for i in range(int((seconds_loud + seconds_quiet) / 0.02)):
            loud = i * 0.02 < seconds_loud
            pcm = [int(random.uniform(-9000, 9000)) if loud else 0 for _ in range(sip.FRAME)]
            self.rtp.sendto(struct.pack("!BBHII", 0x80, 0, seq, seq * 160, 1234) + sip.pcm_to_ulaw(pcm), dest)
            seq += 1
            time.sleep(0.02)


class CodecTest(BrainTest):
    def test_mu_law_round_trip_is_close(self):
        for s in (0, 100, -100, 1000, -5000, 20000, -32768, 32767):
            back = sip.ulaw_to_pcm(sip.pcm_to_ulaw([s]))[0]
            self.assertLessEqual(abs(back - s), max(16, abs(s) // 12), s)

    def test_kokoro_wav_becomes_8khz_mu_law(self):
        self.assertEqual(len(sip.wav_to_ulaw(tone(0.5))), 4000)
        self.assertEqual(sip.wav_to_ulaw(b"RIFFnot really"), b"")

    def test_phone_audio_becomes_16khz_wav_for_whisper(self):
        with wave.open(io.BytesIO(sip.pcm8k_to_wav16k(array.array("h", [0] * 8000)))) as w:
            self.assertEqual((w.getframerate(), w.getnframes()), (16000, 16000))

    def test_compact_headers_are_understood(self):
        msg = sip.Msg.parse(b"BYE sip:x SIP/2.0\r\nv: SIP/2.0/UDP a\r\ni: abc\r\nCSeq: 2 BYE\r\nl: 0\r\n\r\n")
        self.assertEqual((msg.method, msg.get("call-id"), msg.get("via")), ("BYE", "abc", "SIP/2.0/UDP a"))


class SipBase(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        paths.set_config("call_quick_model", "off")
        self.dash = FakeDashboard()
        self.call = call.Call(call.Ears(FAKE_STT), call.Jarvis(self.dash.url))
        self.call.jarvis.voice = lambda text: tone(0.4)
        self.call.start()
        self.server = sip.SipServer(self.call, "127.0.0.1", 0, 0, pw=PW)
        self.server.start()
        self.phone = FakePhone(("127.0.0.1", self.server.port))

    def tearDown(self):
        self.phone.close()
        self.server.stop()
        self.call.ears.stop()
        self.dash.close()
        super().tearDown()

    def wait(self, check, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check():
                return True
            time.sleep(0.05)
        self.fail("timed out")


class SipTest(SipBase):
    def test_the_phone_must_know_the_password_to_register(self):
        self.assertEqual(self.phone.register(password="wrong").status, 401)
        self.assertFalse(self.server.registered())
        self.assertEqual(self.phone.register().status, 200)
        self.assertTrue(self.server.registered())

    def test_a_tailnet_phone_registers_without_answering_the_challenge(self):
        self.server.trusted = [sip.ipaddress.ip_network("127.0.0.0/8")]
        self.phone.request("REGISTER", "reg-t", 1)
        self.assertEqual(self.phone.recv().status, 200)  # Linphone iOS 6 never answered the 401
        self.assertTrue(self.server.registered())
        self.assertIn("is on the tailnet", (paths.home() / "logs" / "sip.log").read_text(encoding="utf-8"))
        self.phone.request("REGISTER", "reg-w", 1, auth='Authorization: Digest username="owner", realm="jarvis", '
                           'nonce="old", uri="sip:jarvis@127.0.0.1", response="0"')
        self.assertEqual(self.phone.recv().status, 401)  # a password that's sent must still be right

    def test_nothing_to_ring_without_a_registered_phone(self):
        self.assertEqual(self.server.ring(ring_for=1), "no phone")

    def test_jarvis_rings_the_phone_and_they_talk(self):
        self.phone.register()
        result = {}
        ringer = threading.Thread(target=lambda: result.update(r=self.server.ring(ring_for=5)))
        ringer.start()
        invite = self.phone.recv("INVITE")
        self.assertIn("m=audio", invite.body)
        self.phone.reply(invite, 180, "Ringing")
        self.phone.reply(invite, 200, "OK", self.phone.sdp())
        self.assertTrue(self.phone.recv("ACK"))
        ringer.join(5)
        self.assertEqual(result["r"], "answered")
        self.wait(lambda: self.phone.loud_frames_heard() > 5)  # the greeting, in JARVIS's voice
        _, port, _ = sip.sdp_media(invite.body)
        time.sleep(0.6)  # the greeting ends
        before = self.phone.loud_frames_heard()  # a short line gets no "mm", so the answer may come back at once
        self.phone.talk(("127.0.0.1", port))
        self.wait(lambda: self.dash.asked)
        self.assertEqual(self.dash.asked, [call.PREFIX + "what did we sell today"])
        self.wait(lambda: self.phone.loud_frames_heard() > before + 5)  # "Four brushes, sir."
        self.assertTrue(any(r.get("via") == "sip" for r in self._latency()))
        self.phone.request("BYE", invite.get("call-id"), 2)
        self.assertEqual(self.phone.recv("200").status, 200)
        self.wait(lambda: not self.call.live())

    def _latency(self):
        import json
        try:
            return [json.loads(l) for l in call.latency_log().read_text(encoding="utf-8").splitlines()]
        except OSError:
            return []

    def test_a_declined_call_is_reported(self):
        self.phone.register()
        result = {}
        ringer = threading.Thread(target=lambda: result.update(r=self.server.ring(ring_for=5)))
        ringer.start()
        invite = self.phone.recv("INVITE")
        self.phone.reply(invite, 603, "Decline")
        self.assertTrue(self.phone.recv("ACK"))
        ringer.join(5)
        self.assertEqual(result["r"], "declined")

    def test_an_unanswered_ring_is_cancelled(self):
        self.phone.register()
        result = {}
        ringer = threading.Thread(target=lambda: result.update(r=self.server.ring(ring_for=1)))
        ringer.start()
        invite = self.phone.recv("INVITE")
        self.phone.reply(invite, 180, "Ringing")
        self.assertTrue(self.phone.recv("CANCEL"))
        ringer.join(5)
        self.assertEqual(result["r"], "no answer")

    def test_the_owner_can_ring_jarvis_from_the_app(self):
        self.phone.request("INVITE", "in-1", 1, body=self.phone.sdp())
        challenge = self.phone.recv("401")
        self.phone.request("INVITE", "in-1", 2, body=self.phone.sdp(),
                           auth=self.phone.authorization(challenge, "INVITE"))
        ok = self.phone.recv("200")
        self.assertIn("m=audio", ok.body)
        self.phone.request("ACK", "in-1", 2)
        self.wait(lambda: self.phone.loud_frames_heard() > 5)
        self.assertTrue(self.call.live())

    def test_every_sign_in_attempt_is_logged_without_the_password(self):
        self.phone.register(password="wrong")
        self.phone.register()
        text = (paths.home() / "logs" / "sip.log").read_text(encoding="utf-8")
        self.assertIn("listening on 127.0.0.1", text)
        self.assertIn(f"in  127.0.0.1:{self.phone.port} REGISTER", text)
        self.assertIn("SIP/2.0 401 Unauthorized", text)
        self.assertIn("sign-in refused: wrong password", text)
        self.assertIn("SIP/2.0 200 OK", text)
        self.assertNotIn(PW, text)
        self.assertNotIn("response=", text)

    def test_a_new_password_replaces_the_old_one(self):
        old = sip.password()
        new = sip.new_password()
        self.assertNotEqual(new, old)
        self.assertEqual(sip.password(), new)

    def test_setup_text_has_the_account_but_not_the_password(self):
        text = sip.setup_text("100.101.102.103")
        self.assertIn("100.101.102.103:5060", text)
        self.assertIn("Username:  owner", text)
        self.assertNotIn(sip.password(), text)


class FakeProvider:
    """A free SIP provider (as sip.linphone.org): challenges JARVIS's account, then puts his call through to the
    owner's phone, which it 'pushes' awake and answers."""

    def __init__(self, user="jarvis-pc", password="prov-pw", answer=200):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(5)
        self.port = self.sock.getsockname()[1]
        self.user, self.password, self.answer = user, password, answer
        self.seen, self.registered, self.answered = [], False, threading.Event()
        self.registers, self.expires = 0, 600
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind(("127.0.0.1", 0))
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self):
        self.running = False
        self.sock.close()
        self.rtp.close()

    def _ok(self, msg, value):
        p = sip.auth_params(value[6:])
        h = lambda s: __import__("hashlib").sha256(s.encode()).hexdigest()  # noqa: E731
        ha1, ha2 = h(f"{self.user}:linphone:{self.password}"), h(f"{msg.method}:{p['uri']}")
        return p.get("username") == self.user and p.get("response") == h(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:auth:{ha2}")

    def _respond(self, msg, addr, code, reason, extra=(), body=""):
        lines = [f"SIP/2.0 {code} {reason}"] + [f"Via: {v}" for v in msg.all("via")] + [
            f"From: {msg.get('from')}", f"To: {msg.get('to')}" + (";tag=prov" if code >= 200 and code != 401 and code != 407 else ""),
            f"Call-ID: {msg.get('call-id')}", f"CSeq: {msg.get('cseq')}", *extra]
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        self.sock.sendto(("\r\n".join(lines) + "\r\n\r\n" + body).encode(), addr)

    def _loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            if not data.strip():
                continue
            msg = sip.Msg.parse(data)
            self.seen.append(msg)
            if not msg.is_request:
                continue
            auth = msg.get("authorization") or msg.get("proxy-authorization")
            challenge = 'Digest realm="linphone", nonce="n1", algorithm=SHA-256, qop="auth"'
            if msg.method == "REGISTER":
                if not auth:
                    self._respond(msg, addr, 401, "Unauthorized", [f"WWW-Authenticate: {challenge}"])
                elif self._ok(msg, auth):
                    self.registered = True
                    contact, extra = msg.get("contact"), []
                    inst = sip.re.search(r'\+sip\.instance="<([^>]+)>"', contact)
                    if inst and "gruu" in msg.get("supported"):  # as Flexisip: the device's own address back
                        extra = [f'Contact: {contact.split(";+sip")[0]};+sip.instance="<{inst.group(1)}>";'
                                 f'expires={self.expires};pub-gruu="sip:{self.user}@sip.linphone.org;gr={inst.group(1)}"']
                    self.registers += 1
                    self._respond(msg, addr, 200, "OK", extra)
                else:
                    self._respond(msg, addr, 403, "Forbidden")
            elif msg.method == "INVITE":
                if not auth:
                    self._respond(msg, addr, 407, "Proxy Authentication Required", [f"Proxy-Authenticate: {challenge}"])
                elif self._ok(msg, auth):
                    self._respond(msg, addr, 180, "Ringing")
                    rr = [f"Record-Route: <sip:127.0.0.1:{self.port};lr>"]
                    if self.answer == 200:
                        self._respond(msg, addr, 200, "OK", rr + ["Contact: <sip:owner-phone@10.0.0.9>"],
                                      sip.sdp("127.0.0.1", self.rtp.getsockname()[1]))
                    elif self.answer:  # 0: the phone just keeps ringing
                        self._respond(msg, addr, self.answer, "Decline")
            elif msg.method == "ACK" and msg.get("route"):
                self.answered.set()


class ProviderTest(SipBase):
    """iPhone ringing through a free provider's push service (sip.linphone.org-style)."""

    def setUp(self):
        BrainTest.setUp(self)
        self.init()
        paths.set_config("call_quick_model", "off")
        self.dash = FakeDashboard()
        self.call = call.Call(call.Ears(FAKE_STT), call.Jarvis(self.dash.url))
        self.call.jarvis.voice = lambda text: tone(0.4)
        self.call.start()
        self.prov = FakeProvider()
        self.server = self.make_server()
        self.server.start()
        self.phone = FakePhone(("127.0.0.1", self.server.port))

    def make_server(self):
        return sip.SipServer(self.call, "127.0.0.1", 0, 0, pw=PW, provider={
            "server": "127.0.0.1", "port": self.prov.port, "user": "jarvis-pc",
            "callee": "sip:owner-phone@sip.linphone.org"}, provider_password="prov-pw")

    def tearDown(self):
        self.prov.close()
        super().tearDown()

    def test_jarvis_signs_in_to_the_provider(self):
        self.wait(lambda: self.server.status()["provider"]["registered"])
        self.assertTrue(self.prov.registered)

    def test_the_ring_goes_through_the_provider_with_auth_and_the_route_set(self):
        self.wait(lambda: self.prov.registered)
        self.assertEqual(self.server.ring(ring_for=5), "answered")
        self.wait(lambda: self.prov.answered.is_set())  # ACK sent back along the Record-Route
        invites = [m for m in self.prov.seen if m.is_request and m.method == "INVITE"]
        self.assertEqual(invites[0].first, "INVITE sip:owner-phone@sip.linphone.org SIP/2.0")
        self.assertTrue(invites[-1].get("proxy-authorization"))
        self.wait(lambda: self.call.live())

    def test_a_declined_push_call_is_reported(self):
        self.prov.answer = 603
        self.wait(lambda: self.prov.registered)
        self.assertEqual(self.server.ring(ring_for=5), "declined")

    def test_strangers_cannot_ring_jarvis_through_the_provider(self):
        stranger = sip.Msg("INVITE sip:jarvis-pc@x SIP/2.0", [("via", "SIP/2.0/UDP 1.2.3.4;branch=z9hG4bKx"),
                           ("from", "<sip:spam@x>;tag=1"), ("to", "<sip:jarvis-pc@x>"), ("call-id", "s1"),
                           ("cseq", "1 INVITE")], sip.sdp("127.0.0.1", 9))
        self.wait(lambda: self.prov.registered)
        port = self.server.psock.getsockname()[1]
        self.prov.sock.sendto(stranger.encode(), ("127.0.0.1", port))
        self.wait(lambda: any(not m.is_request and m.status == 403 for m in self.prov.seen))
        self.assertFalse(self.call.live())


class ProviderSameAccountTest(ProviderTest):
    """The iPhone and JARVIS share one sip.linphone.org account, and JARVIS has only its HA1 (from the Linphone
    desktop app), not the password."""

    def make_server(self):
        key = hashlib.sha256(b"jarvis-pc:linphone:prov-pw").hexdigest()
        return sip.SipServer(self.call, "127.0.0.1", 0, 0, pw=PW, provider={
            "server": "127.0.0.1", "port": self.prov.port, "user": "jarvis-pc",
            "callee": "sip:jarvis-pc@sip.linphone.org"},
            provider_ha1={"user": "jarvis-pc", "realm": "linphone", "algorithm": "SHA-256", "ha1": key})

    def test_jarvis_signs_in_to_the_provider(self):
        self.wait(lambda: self.server.status()["provider"]["registered"])
        self.assertEqual(self.server.status()["provider"]["auth"], "ha1")
        register = [m for m in self.prov.seen if m.is_request and m.method == "REGISTER"][-1]
        self.assertFalse(register.get("contact"))  # no binding: the call can't fork back to JARVIS

    def test_the_ring_goes_through_the_provider_with_auth_and_the_route_set(self):
        self.wait(lambda: self.prov.registered)
        self.assertEqual(self.server.ring(ring_for=5), "answered")
        invites = [m for m in self.prov.seen if m.is_request and m.method == "INVITE"]
        self.assertEqual(invites[0].first, "INVITE sip:jarvis-pc@sip.linphone.org SIP/2.0")
        self.assertTrue(invites[-1].get("proxy-authorization"))

    def test_strangers_cannot_ring_jarvis_through_the_provider(self):
        pass  # same account: the From check is the owner's own account, covered by ProviderTest

    def test_his_own_ring_forked_back_is_refused(self):
        self.wait(lambda: self.prov.registered)
        self.prov.answer = 0  # the phone never answers, so the ring stays open
        self.assertEqual(self.server.ring(wait=False, ring_for=3), "ringing")
        cid = next(iter(self.server.dialogs))
        back = sip.Msg("INVITE sip:jarvis-pc@127.0.0.1 SIP/2.0", [("via", "SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKf"),
                       ("from", "\"JARVIS\" <sip:jarvis-pc@x>;tag=1"), ("to", "<sip:jarvis-pc@x>"), ("call-id", cid),
                       ("cseq", "2 INVITE")], sip.sdp("127.0.0.1", 9))
        self.prov.sock.sendto(back.encode(), ("127.0.0.1", self.server.psock.getsockname()[1]))
        self.wait(lambda: any(not m.is_request and m.status == 482 for m in self.prov.seen))
        self.assertFalse(self.call.live())


class LineTest(ProviderSameAccountTest):
    """The owner rings JARVIS: he is his own device on the shared account and answers calls to its address."""

    INSTANCE = "urn:uuid:0f1e2d3c-aaaa-4bbb-8ccc-123456789abc"

    def make_server(self):
        return sip.SipServer(*self.make_server_args())

    def owner_calls(self, cid, to, frm="sip:jarvis-pc@sip.linphone.org"):
        msg = sip.Msg(f"INVITE sip:jarvis-pc@127.0.0.1:{self.server.psock.getsockname()[1]} SIP/2.0", [
            ("via", f"SIP/2.0/UDP 127.0.0.1:{self.prov.port};branch=z9hG4bK{cid}"),
            ("from", f"<{frm}>;tag=own"), ("to", f"<{to}>"), ("call-id", cid), ("cseq", "1 INVITE"),
            ("contact", "<sip:jarvis-pc@10.0.0.9>")], sip.sdp("127.0.0.1", self.prov.rtp.getsockname()[1]))
        self.prov.sock.sendto(msg.encode(), ("127.0.0.1", self.server.psock.getsockname()[1]))

    def answers(self, cid):
        return [m for m in self.prov.seen if not m.is_request and m.get("call-id") == cid and m.status >= 200]

    def test_jarvis_signs_in_to_the_provider(self):
        self.wait(lambda: self.server.status().get("provider", {}).get("line", {}).get("on"))
        register = [m for m in self.prov.seen if m.is_request and m.method == "REGISTER"][-1]
        self.assertIn(f'+sip.instance="<{self.INSTANCE}>"', register.get("contact"))
        self.assertIn("gruu", register.get("supported"))
        line = self.server.status()["provider"]["line"]
        self.assertEqual(line["address"], f"sip:jarvis-pc@sip.linphone.org;gr={self.INSTANCE}")

    def test_the_owner_rings_jarvis_at_his_own_address_and_he_answers(self):
        self.wait(lambda: self.server.status()["provider"].get("line", {}).get("on"))
        self.owner_calls("own-1", f"sip:jarvis-pc@sip.linphone.org;gr={self.INSTANCE}")
        self.wait(lambda: self.answers("own-1"))
        ok = self.answers("own-1")[0]
        self.assertEqual(ok.status, 200)
        self.assertIn("m=audio", ok.body)
        ack = sip.Msg(f"ACK sip:jarvis-pc@127.0.0.1 SIP/2.0", [
            ("via", f"SIP/2.0/UDP 127.0.0.1:{self.prov.port};branch=z9hG4bKack"), ("from", "<sip:jarvis-pc@x>;tag=own"),
            ("to", ok.get("to")), ("call-id", "own-1"), ("cseq", "1 ACK")])
        self.prov.sock.sendto(ack.encode(), ("127.0.0.1", self.server.psock.getsockname()[1]))
        self.wait(lambda: self.call.live())
        self.assertIn("his own address", (paths.home() / "logs" / "sip.log").read_text(encoding="utf-8"))

    def test_strangers_ringing_his_address_are_refused(self):
        self.wait(lambda: self.prov.registered)
        self.owner_calls("spam-1", f"sip:jarvis-pc@sip.linphone.org;gr={self.INSTANCE}", frm="sip:spam@x")
        self.wait(lambda: self.answers("spam-1"))
        self.assertEqual(self.answers("spam-1")[0].status, 403)
        self.assertFalse(self.call.live())

    def test_a_lapsed_binding_is_signed_in_again_soon(self):
        self.wait(lambda: self.server.status()["provider"].get("line", {}).get("on"))
        self.server.preg["ok"] = False  # as after a failed renewal
        self.assertEqual(self.server._renew_after(), sip.RETRY_AFTER)
        self.server.preg.update(ok=True, expires=120)
        self.assertEqual(self.server._renew_after(), 60)  # half the binding the provider granted
        self.server.preg["at"] = time.time() - 200
        self.assertFalse(self.server.status()["provider"]["line"]["on"])  # older than the binding: lapsed

    def test_a_dropped_provider_connection_signs_the_line_in_again(self):
        self.wait(lambda: self.prov.registers >= 1)
        self.server.stop()
        self.server = type("Fast", (sip.SipServer,), {"KEEPALIVE": 0.05})(*self.make_server_args())
        connects = [1]
        self.server._connects = lambda: connects[0]  # StreamSock counts its connections
        self.server.start()
        self.wait(lambda: self.server.status()["provider"].get("line", {}).get("on"))
        before = self.prov.registers
        time.sleep(0.3)
        self.assertEqual(self.prov.registers, before)  # nothing new: no sign-in
        connects[0] = 2  # the provider dropped the connection and the next send reconnected
        self.wait(lambda: self.prov.registers > before)

    def make_server_args(self):
        key = hashlib.sha256(b"jarvis-pc:linphone:prov-pw").hexdigest()
        return (self.call, "127.0.0.1", 0, 0, PW, "", {
            "server": "127.0.0.1", "port": self.prov.port, "user": "jarvis-pc",
            "callee": "sip:jarvis-pc@sip.linphone.org", "line": True, "instance": self.INSTANCE}, "", (),
            {"user": "jarvis-pc", "realm": "linphone", "algorithm": "SHA-256", "ha1": key})


class TcpProviderTest(ProviderTest):
    """The same push ring over TCP: a real phone's 200 OK (ICE candidates) is too big for one UDP packet."""

    def setUp(self):
        self.relay = None
        super().setUp()

    def tearDown(self):
        if self.relay:
            self.relay.close()
        super().tearDown()

    def _server(self):
        # a TCP front for the UDP fake: each SIP message in either direction is passed across unchanged
        self.relay = socket.socket()
        self.relay.bind(("127.0.0.1", 0))
        self.relay.listen(1)
        up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        up.bind(("127.0.0.1", 0))

        def down(conn):
            while True:
                try:
                    conn.sendall(up.recv(65535))
                except OSError:
                    return

        def run():
            conn, _ = self.relay.accept()
            threading.Thread(target=down, args=(conn,), daemon=True).start()
            framer = sip.StreamSock.__new__(sip.StreamSock)
            framer.buf, framer.conn, framer.closed, framer.timeout = b"", conn, False, 5
            framer.server, framer.port = "127.0.0.1", 0
            while True:
                try:
                    msg, _ = framer.recvfrom(0)
                except socket.timeout:
                    continue
                except OSError:
                    return
                up.sendto(msg, ("127.0.0.1", self.prov.port))
        threading.Thread(target=run, daemon=True).start()
        return self.relay.getsockname()[1]

    def make_server(self):
        return sip.SipServer(self.call, "127.0.0.1", 0, 0, pw=PW, provider={
            "server": "127.0.0.1", "port": self._server(), "user": "jarvis-pc", "transport": "tcp",
            "callee": "sip:owner-phone@sip.linphone.org"}, provider_password="prov-pw")

    def test_strangers_cannot_ring_jarvis_through_the_provider(self):
        pass  # covered over UDP

    def test_messages_are_split_out_of_the_stream(self):
        a = sip.Msg("OPTIONS sip:x SIP/2.0", [("call-id", "1")], "hello").encode()
        b = sip.Msg("OPTIONS sip:x SIP/2.0", [("call-id", "2")]).encode()
        framer = sip.StreamSock.__new__(sip.StreamSock)
        framer.buf = b"\r\n\r\n" + a + b
        self.assertEqual(framer._message(), a)
        self.assertEqual(framer._message(), b)
        self.assertIsNone(framer._message())


class KeepLineTest(BrainTest):
    """The routines tick keeps the call server and JARVIS's line up, and never restarts it mid-call."""

    def setUp(self):
        super().setUp()
        self.init()
        self.started, self.quit, self.status, self.up = [], [], {}, True
        self.saved = call.running, call.sip_request, call.start_background, call.urllib.request.urlopen
        call.running = lambda port=call.PORT: self.up
        call.sip_request = lambda body, timeout=5: self.status
        call.start_background = lambda: self.started.append(1)

        def urlopen(req, timeout=0):
            self.quit.append(req.full_url)
            self.up = False
            return io.BytesIO(b"{}")
        call.urllib.request.urlopen = urlopen

    def tearDown(self):
        call.running, call.sip_request, call.start_background, call.urllib.request.urlopen = self.saved
        super().tearDown()

    def line(self, on, up_for=600):
        paths.set_config("sip_bind", "100.89.49.110")
        paths.set_config("sip_provider", {"user": "u", "server": "s", "line": True, "instance": "urn:uuid:1"})
        self.status = {"enabled": True, "on_call": False,
                       "provider": {"error": "", "line": {"on": on, "up_for": up_for, "address": "x"}}}

    def test_off_without_a_phone_line(self):
        self.assertEqual(call.keep_line(), "phone line off")

    def test_a_server_that_is_down_is_started(self):
        paths.set_config("sip_bind", "100.89.49.110")
        self.up = False
        self.assertIn("started", call.keep_line())
        self.assertEqual(self.started, [1])

    def test_a_signed_in_line_is_left_alone(self):
        self.line(True)
        self.assertEqual(call.keep_line(), "line signed in")
        self.assertEqual((self.started, self.quit), ([], []))

    def test_a_lapsed_line_restarts_the_server(self):
        self.line(False)
        self.assertIn("lapsed", call.keep_line())
        self.assertTrue(self.quit[0].endswith("/api/call/quit"))
        self.assertEqual(self.started, [1])

    def test_a_server_on_old_settings_restarts(self):
        self.line(True)
        self.status["provider"].pop("line")  # started before --line
        self.assertIn("old settings", call.keep_line())

    def test_never_mid_call_or_while_still_signing_in(self):
        self.line(False)
        self.status["on_call"] = True
        self.assertEqual(call.keep_line(), "on a call")
        self.line(False, up_for=20)
        self.assertEqual(call.keep_line(), "line signing in")
        self.assertEqual(self.quit, [])


class LinphoneImportTest(BrainTest):
    def test_the_desktop_apps_ha1_is_copied_to_the_secrets(self):
        self.init()
        rc = paths.home() / "linphonerc"
        rc.write_text("[proxy_0]\nrealm=sip.linphone.org\n\n[auth_info_0]\nusername=marley\nha1=abc123\n"
                      "realm=sip.linphone.org\ndomain=sip.linphone.org\nalgorithm=SHA-256\n", encoding="utf-8")
        acct = sip.import_linphone(str(rc))
        self.assertEqual(acct, {"user": "marley", "realm": "sip.linphone.org", "algorithm": "SHA-256"})
        self.assertEqual(sip.provider_ha1()["ha1"], "abc123")
