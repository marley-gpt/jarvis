"""JARVIS rings the owner's phone like a real call: a tiny SIP server here, a free SIP app on the phone. No number.

The owner turned down a second SIM or Telegram account (M-261109-b234). So the phone runs a free softphone
(Linphone, or Zoiper Free), registered to this PC as its SIP server. When JARVIS rings, the phone shows its own
incoming-call screen; when the owner answers, the audio runs through the same call as the call page (call.py):
local Whisper, the quick brain, Kokoro. The owner can also ring JARVIS from the app.

Everything is stdlib: SIP over UDP (REGISTER with digest auth, INVITE / ACK / BYE / CANCEL both ways), RTP with
G.711 mu-law (PCMU, 8 kHz, which every softphone speaks), and a voice-activity detector for turn-taking. The phone
reaches the PC over Tailscale (free, private, works on mobile data); a Cloudflare quick tunnel can't carry it
(HTTP only). Nothing listens unless "sip_bind" is set in brain/config.json (`bin/jarvis call sip --bind <ip>`).

A phone call's audio goes through the phone's own echo canceller (the same one a normal call uses), so JARVIS's
voice doesn't come back to him the way it does from a browser tab on speaker.
"""

from __future__ import annotations

import array
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import math
import queue
import random
import re
import secrets
import socket
import struct
import sys
import threading
import time
import wave

from . import paths

REALM = "jarvis"
USER = "owner"          # the account the phone signs in as
JARVIS_USER = "jarvis"  # who the call comes from
PORT = 5060
RTP_PORT = 40000
FRAME = 160             # 20 ms at 8 kHz
RING_FOR = 45.0         # seconds before an unanswered ring is cancelled
DEAD_AFTER = 30.0       # seconds without the phone's audio before a call counts as dropped
LOG_MAX = 1_000_000     # bytes before brain/logs/sip.log rolls over to sip.log.1
LINE_EXPIRES = 600      # seconds JARVIS asks the provider to keep his own line's binding; renewed at half that
RETRY_AFTER = 30.0      # seconds before a failed provider sign-in is tried again
TRUSTED = ("100.64.0.0/10",)  # Tailscale: only the owner's own signed-in devices have these addresses
LOG_HEADERS = ("via", "from", "to", "contact", "expires", "user-agent")


def password() -> str:
    """The SIP account's password, kept with the other secrets. Made on first use."""
    path = paths.home() / "secrets" / "sip_password"
    try:
        pw = path.read_text(encoding="utf-8").strip()
    except OSError:
        pw = ""
    if not pw:
        pw = secrets.token_urlsafe(18)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pw, encoding="utf-8")
    return pw


def new_password() -> str:
    """A fresh SIP account password, replacing one that leaked. The running server keeps the old one until it
    restarts."""
    path = paths.home() / "secrets" / "sip_password"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secrets.token_urlsafe(18), encoding="utf-8")
    return password()


def settings() -> dict:
    cfg = paths.config()
    return {"bind": cfg.get("sip_bind") or "", "port": int(cfg.get("sip_port") or PORT),
            "rtp_port": int(cfg.get("sip_rtp_port") or RTP_PORT), "provider": cfg.get("sip_provider") or {},
            "trusted": list(cfg.get("sip_trusted", TRUSTED))}


def provider_password() -> str:
    """JARVIS's own account password at the SIP provider (brain/secrets/sip_provider_password), typed there by
    the owner: never in chat, never in config."""
    try:
        return (paths.home() / "secrets" / "sip_provider_password").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def provider_ha1() -> dict:
    """The provider account's stored digest key instead of a password (brain/secrets/sip_provider_ha1:
    {"user", "realm", "algorithm", "ha1"}). A Linphone app signed in to the account keeps only this, never the
    password, and it is all a digest answer needs."""
    try:
        data = json.loads((paths.home() / "secrets" / "sip_provider_ha1").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("ha1") else {}


def line_address(provider: dict | None = None) -> str:
    """The address the owner dials to reach JARVIS: the shared account plus JARVIS's own device (a GRUU, RFC 5627),
    so the call goes to him alone and never rings the owner's own phone. '' until the line is on."""
    p = provider if provider is not None else settings()["provider"]
    if not (p.get("line") and p.get("instance") and p.get("user") and p.get("server")):
        return ""
    return f"sip:{p['user']}@{p['server']};gr={p['instance']}"


def import_linphone(rc_path: str = "") -> dict:
    """Copy the sip.linphone.org account's HA1 from the Linphone desktop app on this PC (linphonerc, the first
    [auth_info_N] with an ha1) into brain/secrets/sip_provider_ha1. Returns it without the key."""
    import configparser
    import os
    rc = rc_path or os.path.join(os.environ.get("LOCALAPPDATA", ""), "linphone", "linphonerc")
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    if not cp.read(rc, encoding="utf-8"):
        raise FileNotFoundError(f"no Linphone settings at {rc}")
    for name in sorted(s for s in cp.sections() if s.startswith("auth_info_")):
        a = cp[name]
        if a.get("ha1") and a.get("username"):
            data = {"user": a["username"], "realm": a.get("realm") or a.get("domain", ""),
                    "algorithm": (a.get("algorithm") or "MD5").upper(), "ha1": a["ha1"].strip()}
            path = paths.home() / "secrets" / "sip_provider_ha1"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
            return {k: v for k, v in data.items() if k != "ha1"}
    raise ValueError(f"no account with an ha1 in {rc}")


_log_lock = threading.Lock()


def log(line: str) -> None:
    """One line in brain/logs/sip.log: every SIP packet in and out, so a phone that can't sign in shows why
    (or shows that nothing arrived at all). Passwords and digest responses never go in it."""
    path = paths.home() / "logs" / "sip.log"
    try:
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_MAX:
                path.replace(path.with_name("sip.log.1"))
            with path.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def describe(msg: "Msg") -> str:
    """A packet's first line and the headers that explain a failed sign-in."""
    parts = [msg.first]
    for name in LOG_HEADERS:
        if msg.get(name):
            parts.append(f"{name}: {msg.get(name)}")
    for name in ("authorization", "proxy-authorization", "www-authenticate", "proxy-authenticate"):
        value = msg.get(name)
        if value:
            p = auth_params(value[6:]) if value.lower().startswith("digest") else {}
            parts.append(f"{name}: user={p.get('username', '')} realm={p.get('realm', '')} uri={p.get('uri', '')}")
    return " | ".join(parts)


def tailscale_ip() -> str:
    """This PC's Tailscale address (100.64.0.0/10), if Tailscale is running here."""
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            a, b = (int(x) for x in ip.split(".")[:2])
            if a == 100 and 64 <= b <= 127:
                return ip
    except (OSError, ValueError):
        pass
    return ""


# ---------------------------------------------------------------- G.711 mu-law and resampling

def _ulaw_decode(b: int) -> int:
    b = ~b & 0xFF
    t = (((b & 0x0F) << 3) + 0x84) << ((b & 0x70) >> 4)
    return (0x84 - t) if b & 0x80 else (t - 0x84)


def _ulaw_encode(s: int) -> int:
    sign = 0x80 if s < 0 else 0
    s = min(32635, -s if s < 0 else s) + 0x84
    exp = max(0, s.bit_length() - 8)
    exp = min(exp, 7)
    mant = (s >> (exp + 3)) & 0x0F
    return ~(sign | (exp << 4) | mant) & 0xFF


DECODE = array.array("h", [_ulaw_decode(i) for i in range(256)])
ENCODE = bytes(_ulaw_encode(s - 65536 if s >= 32768 else s) for s in range(65536))
SILENCE = bytes([0xFF]) * FRAME


def ulaw_to_pcm(data: bytes) -> array.array:
    return array.array("h", (DECODE[b] for b in data))


def pcm_to_ulaw(samples) -> bytes:
    return bytes(ENCODE[s & 0xFFFF] for s in samples)


def resample(samples, src: int, dst: int) -> array.array:
    """Linear resampling; averages when going down so 24 kHz speech doesn't alias into 8 kHz."""
    if src == dst or not samples:
        return array.array("h", samples)
    n = int(len(samples) * dst / src)
    out = array.array("h", bytes(2 * n))
    ratio = src / dst
    if ratio > 1:
        for i in range(n):
            a = int(i * ratio)
            b = min(len(samples), max(a + 1, int((i + 1) * ratio)))
            out[i] = sum(samples[a:b]) // (b - a)
    else:
        last = len(samples) - 1
        for i in range(n):
            x = i * ratio
            a = int(x)
            f = x - a
            out[i] = int(samples[a] * (1 - f) + samples[min(a + 1, last)] * f)
    return out


def wav_to_ulaw(wav: bytes) -> bytes:
    """Kokoro's WAV (any rate, 16-bit) to 8 kHz mu-law. b'' if it isn't a WAV this can read."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            if w.getsampwidth() != 2:
                return b""
            rate, channels = w.getframerate(), w.getnchannels()
            raw = array.array("h", w.readframes(w.getnframes()))
    except (wave.Error, EOFError, ValueError):
        return b""
    if channels > 1:
        raw = array.array("h", raw[::channels])
    return pcm_to_ulaw(resample(raw, rate, 8000))


def pcm8k_to_wav16k(samples) -> bytes:
    """The owner's speech, 8 kHz from the phone, as the 16 kHz WAV Whisper takes."""
    up = resample(samples, 8000, 16000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(up.tobytes())
    return buf.getvalue()


def rms(samples) -> float:
    return math.sqrt(sum(s * s for s in samples) / max(1, len(samples))) / 32768.0 if samples else 0.0


# ---------------------------------------------------------------- SIP messages

COMPACT = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length",
           "c": "content-type", "k": "supported"}


class Msg:
    def __init__(self, first: str, headers: list[tuple[str, str]], body: str = ""):
        self.first, self.headers, self.body = first, headers, body

    @classmethod
    def parse(cls, data: bytes) -> "Msg | None":
        try:
            text = data.decode("utf-8", "replace")
        except Exception:
            return None
        head, _, body = text.partition("\r\n\r\n")
        lines = head.split("\r\n")
        if not lines or not lines[0].strip():
            return None
        headers = []
        for line in lines[1:]:
            if line[:1] in " \t" and headers:  # folded header
                headers[-1] = (headers[-1][0], headers[-1][1] + " " + line.strip())
                continue
            name, _, value = line.partition(":")
            name = name.strip().lower()
            headers.append((COMPACT.get(name, name), value.strip()))
        return cls(lines[0], headers, body)

    @property
    def is_request(self) -> bool:
        return not self.first.startswith("SIP/2.0")

    @property
    def method(self) -> str:
        return self.first.split(" ", 1)[0] if self.is_request else self.cseq_method

    @property
    def status(self) -> int:
        try:
            return int(self.first.split(" ")[1]) if not self.is_request else 0
        except (IndexError, ValueError):
            return 0

    @property
    def uri(self) -> str:
        parts = self.first.split(" ")
        return parts[1] if len(parts) > 1 else ""

    def get(self, name: str, default: str = "") -> str:
        for k, v in self.headers:
            if k == name:
                return v
        return default

    def all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k == name]

    @property
    def cseq_method(self) -> str:
        parts = self.get("cseq").split()
        return parts[1].upper() if len(parts) > 1 else ""

    def encode(self) -> bytes:
        body = self.body.encode()
        lines = [self.first] + [f"{k.title() if k != 'call-id' else 'Call-ID'}: {v}" for k, v in self.headers
                                if k != "content-length"]
        lines.append(f"Content-Length: {len(body)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def tag_of(value: str) -> str:
    m = re.search(r";\s*tag=([^;>\s]+)", value)
    return m.group(1) if m else ""


def uri_in(value: str) -> str:
    m = re.search(r"<([^>]+)>", value)
    return m.group(1) if m else value.split(";")[0].strip()


def user_of(value: str) -> str:
    m = re.search(r"sips?:([^@;>]+)@", value)
    return m.group(1) if m else ""


def sdp(ip: str, port: int) -> str:
    sid = str(int(time.time()))
    return (f"v=0\r\no=jarvis {sid} {sid} IN IP4 {ip}\r\ns=JARVIS\r\nc=IN IP4 {ip}\r\nt=0 0\r\n"
            f"m=audio {port} RTP/AVP 0 101\r\na=rtpmap:0 PCMU/8000\r\na=rtpmap:101 telephone-event/8000\r\n"
            f"a=fmtp:101 0-15\r\na=ptime:20\r\na=sendrecv\r\n")


def sdp_media(body: str) -> tuple[str, int, bool]:
    """(ip, port, offers PCMU) from the phone's SDP."""
    ip = ""
    port = 0
    pcmu = False
    for line in body.splitlines():
        if line.startswith("c=IN IP4 "):
            ip = line[9:].strip()
        elif line.startswith("m=audio "):
            parts = line.split()
            port = int(parts[1])
            pcmu = "0" in parts[3:]
    return ip, port, pcmu


def digest(user: str, pw: str, method: str, uri: str, nonce: str, nc: str = "", cnonce: str = "",
           qop: str = "") -> str:
    md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731  (SIP digest is MD5 by spec)
    ha1, ha2 = md5(f"{user}:{REALM}:{pw}"), md5(f"{method}:{uri}")
    if qop:
        return md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return md5(f"{ha1}:{nonce}:{ha2}")


def auth_params(value: str) -> dict:
    return {k.lower(): v.strip('"') for k, v in re.findall(r'(\w+)\s*=\s*("[^"]*"|[^,\s]+)', value)}


# ---------------------------------------------------------------- RTP

class Rtp:
    """One call's audio: 20 ms mu-law frames out at a steady pace, frames in handed to on_audio."""

    def __init__(self, bind: str, port: int, on_audio):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # The usual port, else the next free one in the range the firewall rule opens, else any (the SDP says which).
        for p in ([port] + list(range(port + 2, port + 101, 2)) if port else []) + [0]:
            try:
                self.sock.bind((bind, p))
                break
            except OSError:
                continue
        self.port = self.sock.getsockname()[1]
        self.on_audio = on_audio
        self.remote: tuple[str, int] | None = None
        self.out: queue.Queue = queue.Queue()
        self.seq, self.ts, self.ssrc = random.randint(0, 65535), random.randint(0, 2 ** 31), random.getrandbits(32)
        self.running = True
        self.talking = False  # JARVIS's audio is going out
        self.on_first_frame = None  # called when a tagged reply starts to go out (latency)
        self.on_dead = None  # called once if the phone's audio stops for DEAD_AFTER seconds (a lost hang-up)
        self.last_in = 0.0
        self.public: tuple[str, int] | None = None  # this socket's internet address, if STUN found it

    def start(self) -> None:
        threading.Thread(target=self._recv, name="rtp-in", daemon=True).start()
        threading.Thread(target=self._send, name="rtp-out", daemon=True).start()

    def play(self, ulaw: bytes, tag=None) -> None:
        for i in range(0, len(ulaw), FRAME):
            chunk = ulaw[i:i + FRAME]
            self.out.put((chunk + SILENCE[len(chunk):], tag if i == 0 else None))

    def flush(self) -> None:
        """Stop talking now (the owner talked over JARVIS)."""
        try:
            while True:
                self.out.get_nowait()
        except queue.Empty:
            pass

    def _send(self) -> None:
        nxt, marker = time.perf_counter(), True
        while self.running:
            try:
                frame, tag = self.out.get_nowait()
                if not self.talking:
                    marker = True
                self.talking = True
                if tag is not None and self.on_first_frame:
                    self.on_first_frame(tag)
            except queue.Empty:
                frame, tag = SILENCE, None
                self.talking = False
            if self.last_in and time.time() - self.last_in > DEAD_AFTER and self.on_dead:
                dead, self.on_dead = self.on_dead, None
                threading.Thread(target=dead, daemon=True).start()
            if self.remote:
                header = struct.pack("!BBHII", 0x80, (0x80 if marker else 0) | 0, self.seq, self.ts, self.ssrc)
                try:
                    self.sock.sendto(header + frame, self.remote)
                except OSError:
                    pass
                marker = False
            self.seq = (self.seq + 1) & 0xFFFF
            self.ts = (self.ts + FRAME) & 0xFFFFFFFF
            nxt += 0.02
            time.sleep(max(0.0, nxt - time.perf_counter()))

    def _recv(self) -> None:
        self.sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(data) < 12 or data[0] >> 6 != 2:
                continue
            self.last_in = time.time()
            if self.remote is None or addr[0] == self.remote[0]:
                self.remote = addr  # symmetric RTP: answer where the audio really comes from
            cc, ext, pt = data[0] & 0x0F, data[0] & 0x10, data[1] & 0x7F
            off = 12 + 4 * cc
            if ext and len(data) >= off + 4:
                off += 4 + 4 * struct.unpack("!H", data[off + 2:off + 4])[0]
            if pt == 0:
                self.on_audio(data[off:])

    def stop(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- the phone on a call

class PhoneLeg:
    """The owner on a SIP call: their audio in (turn-taking by voice activity), JARVIS's voice out."""

    END_MS, START_MS, BARGE_MS, MIN_MS = 550, 60, 140, 350

    def __init__(self, call, rtp: Rtp):
        self.call, self.rtp = call, rtp
        self.q = call.subscribe()
        self.running = True
        self.floor, self.loud_ms, self.quiet_ms, self.heard_ms = 0.004, 0.0, 0.0, 0.0
        self.capturing, self.during = False, False
        self.frames: list = []
        self.pre: list = []
        self.quiet_until = 0.0
        self.clips: dict[str, list[bytes]] = {}
        self.pending: dict[int, tuple[float, float]] = {}  # turn -> (owner's last word, vad wait)
        self.last_clip = {}
        self.acked = False
        rtp.on_audio = self.audio
        rtp.on_first_frame = self._first_sound

    def start(self) -> None:
        threading.Thread(target=self._events, name="sip-leg", daemon=True).start()
        self.call.hello()

    def stop(self) -> None:
        self.running = False
        self.call.unsubscribe(self.q)
        self.rtp.stop()

    def _events(self) -> None:
        while self.running:
            try:
                ev = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            if ev.get("type") == "say" and ev.get("wav"):
                ulaw = wav_to_ulaw(base64.b64decode(ev["wav"]))
                if ulaw:
                    self.rtp.play(ulaw, ev.get("turn") or None)
            elif ev.get("type") == "clips":
                self.clips = {k: [u for u in (wav_to_ulaw(base64.b64decode(c["wav"])) for c in v) if u]
                              for k, v in ev["clips"].items()}

    def _first_sound(self, turn: int) -> None:
        if turn in self.pending:
            end_at, vad = self.pending.pop(turn)
            self.call.page_latency(turn, {"vad_ms": vad, "heard_to_voice_ms": (time.time() - end_at) * 1000})

    def _clip(self, kind: str) -> None:
        options = self.clips.get(kind) or []
        if options and not self.rtp.talking and self.rtp.out.empty():
            i = random.randrange(len(options))
            if len(options) > 1 and i == self.last_clip.get(kind):
                i = (i + 1) % len(options)
            self.last_clip[kind] = i
            self.rtp.play(options[i])

    def audio(self, payload: bytes) -> None:
        """One RTP frame from the phone: the same turn-taking as the call page."""
        pcm = ulaw_to_pcm(payload)
        level, ms = rms(pcm), len(payload) / 8.0
        talking = self.rtp.talking or not self.rtp.out.empty()
        if talking:
            self.quiet_until = time.time() + 0.5
        jarvis = talking or time.time() < self.quiet_until
        low, high = max(0.012, self.floor * 3.5), max(0.06, self.floor * 12)
        if not self.capturing:
            self.pre.append(pcm)
            if len(self.pre) * ms > 400:
                self.pre.pop(0)
            if not jarvis and level < self.floor * 2.5:
                self.floor = max(0.002, self.floor * 0.96 + level * 0.04)
            self.loud_ms = self.loud_ms + ms if level > (high if jarvis else low) else 0.0
            if self.loud_ms >= (self.BARGE_MS if jarvis else self.START_MS):
                self.during = jarvis
                if talking:
                    self.rtp.flush()
                    self.call.hush()
                self.capturing, self.frames, self.pre = True, list(self.pre), []
                self.heard_ms = self.quiet_ms = 0.0
            return
        self.frames.append(pcm)
        if level > low * 0.6:
            self.heard_ms += ms
            self.quiet_ms = 0.0
        else:
            self.quiet_ms += ms
        if self.quiet_ms > self.END_MS or len(self.frames) * ms > 30000:
            self._finish()

    def _finish(self) -> None:
        vad, end_at = self.quiet_ms, time.time() - self.quiet_ms / 1000
        frames, self.frames, self.capturing = self.frames, [], False
        self.quiet_ms = 0.0
        if self.heard_ms < self.MIN_MS:
            return
        samples = array.array("h")
        for f in frames:
            samples.extend(f)
        turn = self.call.new_turn("sip", vad_s=round(vad / 1000, 3))
        self.pending[turn] = (end_at, vad)
        # A quick "mm" for a proper stretch of speech, but never two turns running: every turn sounds scripted.
        self.acked = self.heard_ms >= 1500 and not self.acked
        if self.acked:
            self._clip("ack")
        threading.Thread(target=self.call.heard, args=(pcm8k_to_wav16k(samples), turn, self.during),
                         daemon=True).start()


# ---------------------------------------------------------------- the SIP server

class Dialog:
    def __init__(self, call_id: str, local_tag: str, remote_uri: str, addr, remote_tag: str = "",
                 incoming: bool = False, sock=None):
        self.call_id, self.local_tag, self.remote_tag = call_id, local_tag, remote_tag
        self.remote_uri, self.addr, self.incoming, self.sock = remote_uri, addr, incoming, sock
        self.cseq = 1
        self.leg: PhoneLeg | None = None
        self.answered = threading.Event()
        self.provisional = threading.Event()
        self.result = ""
        self.invite: Msg | None = None
        self.routes: list[str] = []  # the route set from the provider's Record-Route (provider calls)
        self.rtp: Rtp | None = None


def stun_address(sock: socket.socket, server=("stun.linphone.org", 3478), timeout: float = 2.0) -> tuple[str, int]:
    """This socket's public address as the internet sees it (a STUN binding request, RFC 5389). ('', 0) if unknown."""
    tid = secrets.token_bytes(12)
    try:
        old = sock.gettimeout()
        sock.settimeout(timeout)
        sock.sendto(struct.pack("!HHI", 1, 0, 0x2112A442) + tid, server)
        data, _ = sock.recvfrom(2048)
        sock.settimeout(old)
    except OSError:
        return "", 0
    if data[8:20] != tid:
        return "", 0
    i = 20
    while i + 4 <= len(data):
        kind, size = struct.unpack("!HH", data[i:i + 4])
        value = data[i + 4:i + 4 + size]
        if kind == 0x0020 and len(value) >= 8:  # XOR-MAPPED-ADDRESS
            port = struct.unpack("!H", value[2:4])[0] ^ 0x2112
            ip = socket.inet_ntoa(bytes(b ^ m for b, m in zip(value[4:8], struct.pack("!I", 0x2112A442))))
            return ip, port
        i += 4 + size + (-size % 4)
    return "", 0


class StreamSock:
    """SIP over TCP or TLS to the provider, behind the same sendto / recvfrom as the UDP socket. A phone's 200 OK
    carries ICE candidates and runs past one UDP packet; the fragments never got through the home router
    (M-261109 test ring), and a stream has no size limit. Reconnects when the provider drops it."""

    def __init__(self, server: str, port: int, tls: bool):
        self.server, self.port, self.tls = server, port, tls
        self.conn: socket.socket | None = None
        self.buf = b""
        self.timeout = 0.5
        self.wlock = threading.Lock()
        self.closed = False
        self.connects = 0  # a new connection loses the provider's binding to the old one: re-register then
        self._connect()

    def _connect(self) -> None:
        conn = socket.create_connection((self.server, self.port), timeout=10)
        if self.tls:
            import ssl
            conn = ssl.create_default_context().wrap_socket(conn, server_hostname=self.server)
        conn.settimeout(self.timeout)
        self.conn, self.buf = conn, b""
        self.connects += 1

    def settimeout(self, t: float) -> None:
        self.timeout = t
        if self.conn:
            self.conn.settimeout(t)

    def getsockname(self):
        return self.conn.getsockname() if self.conn else ("0.0.0.0", 0)

    def sendto(self, data: bytes, addr=None) -> None:
        with self.wlock:
            if self.closed:
                raise OSError("closed")
            if self.conn is None:
                self._connect()
            try:
                self.conn.sendall(data)
            except OSError:
                self.conn = None
                raise

    def _message(self) -> bytes | None:
        self.buf = self.buf.lstrip(b"\r\n")  # CRLF keep-alives
        head, sep, rest = self.buf.partition(b"\r\n\r\n")
        if not sep:
            return None
        m = re.search(rb"(?im)^(?:content-length|l)\s*:\s*(\d+)", head)
        size = int(m.group(1)) if m else 0
        if len(rest) < size:
            return None
        self.buf = rest[size:]
        return head + sep + rest[:size]

    def recvfrom(self, _n: int):
        while True:
            msg = self._message()
            if msg is not None:
                return msg, (self.server, self.port)
            if self.closed:
                raise OSError("closed")
            conn = self.conn
            if conn is None:
                time.sleep(self.timeout)
                raise socket.timeout()
            try:
                data = conn.recv(65535)
            except (socket.timeout, TimeoutError):
                raise socket.timeout()
            except OSError:
                data = b""
            if not data:  # the provider hung up the connection: the next send reconnects
                if self.conn is conn:
                    self.conn = None
                log(f"provider connection to {self.server}:{self.port} closed")
                raise socket.timeout()
            self.buf += data

    def close(self) -> None:
        self.closed = True
        if self.conn:
            try:
                self.conn.close()
            except OSError:
                pass


class SipServer:
    """A registrar for the owner's softphone over Tailscale, calls to and from it, and (optional) calls through a
    free SIP provider such as sip.linphone.org, whose push service makes an iPhone ring even when the app is shut.

    "sip_provider" in brain/config.json: {"server": "sip.linphone.org", "port": 5060, "user": "<JARVIS's account>",
    "callee": "sip:<owner's account>@sip.linphone.org", "media": "tailscale" | "stun"}; the account password lives in
    brain/secrets/sip_provider_password, or its HA1 in brain/secrets/sip_provider_ha1. Media normally still runs
    over Tailscale ("tailscale"); "stun" advertises the PC's public address instead, if the provider insists on
    relaying.

    The callee may be JARVIS's own account (the owner's iPhone signed in to it): the provider then forks JARVIS's
    INVITE to every device on the account, push included. JARVIS registers without a Contact then (only checks
    the sign-in and lists the bindings), so the call can't fork back to him.

    With "line": true and an "instance" (urn:uuid) he registers a Contact after all, as his own device on the
    account, and the provider hands back that device's own address (pub-gruu, RFC 5627). The owner's "JARVIS"
    contact in Linphone dials it: the call reaches JARVIS alone and he answers. His own rings forked back still get
    482 (by Call-ID); anyone but the owner's account still gets 403. Sign-up for a separate account at
    sip.linphone.org sits behind an hCaptcha, which is why the line lives on the owner's account (D68)."""

    KEEPALIVE = 25.0

    def __init__(self, call, bind: str, port: int = PORT, rtp_port: int = RTP_PORT, pw: str | None = None,
                 advertise: str = "", provider: dict | None = None, provider_password: str = "",
                 trusted=(), provider_ha1: dict | None = None):
        self.call = call
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, port))
        self.bind, self.port = bind, self.sock.getsockname()[1]
        self.ip = advertise or (bind if bind not in ("0.0.0.0", "") else tailscale_ip() or "127.0.0.1")
        self.rtp_port, self.pw = rtp_port, pw or password()
        self.contact: dict = {}  # the registered phone: uri, addr, expires
        self.nonces: dict[str, float] = {}
        # addresses that may sign in without the password: the tailnet, where Tailscale has already proved the
        # device is the owner's. Linphone iOS 6 never answered the 401 (M-261109-28c1), so this is the way in.
        self.trusted = [ipaddress.ip_network(n, strict=False) for n in trusted]
        self.dialogs: dict[str, Dialog] = {}
        self.running = True
        self.lock = threading.Lock()
        self.tl = threading.local()  # which socket the request being handled came in on
        self.last_registered = 0.0
        self.provider = dict(provider or {})
        self.provider_pw = provider_password
        self.provider_ha1 = dict(provider_ha1 or {})
        # ringing JARVIS's own account: don't add a binding, or the provider forks the call back to JARVIS
        self.same_account = bool(self.provider.get("user")) and user_of(self.provider.get("callee", "")) == \
            self.provider.get("user")
        # JARVIS's own device on the account, which the owner rings (see the class notes)
        self.instance = self.provider.get("instance", "") if self.provider.get("line") else ""
        self.started = time.time()
        self.ptransport = (self.provider.get("transport") or "udp").upper()  # udp, tcp or tls
        self.psock: socket.socket | None = None  # the provider's socket: out to the internet, not the tailnet
        self.paddr: tuple[str, int] | None = None
        self.preg = {"ok": False, "cseq": 0, "call_id": secrets.token_hex(10), "tag": secrets.token_hex(4),
                     "error": ""}

    # plumbing
    def start(self) -> None:
        log(f"listening on {self.bind}:{self.port} UDP, advertising {self.ip}")
        threading.Thread(target=self._loop, args=(self.sock,), name="sip", daemon=True).start()
        if self.provider.get("server") and self.provider.get("user"):
            transport = self.ptransport
            port = int(self.provider.get("port") or (5061 if transport == "TLS" else 5060))
            try:
                if transport == "UDP":
                    self.psock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self.psock.bind(("0.0.0.0", 0))
                else:
                    self.psock = StreamSock(self.provider["server"], port, tls=transport == "TLS")
                self.paddr = (socket.gethostbyname(self.provider["server"]), port)
                log(f"provider {self.provider['server']}:{port} over {transport}")
            except OSError as exc:
                self.preg["error"] = f"provider unreachable: {exc}"
                self.psock = None
                return
            threading.Thread(target=self._loop, args=(self.psock,), name="sip-provider", daemon=True).start()
            threading.Thread(target=self._provider_keepalive, name="sip-provider-reg", daemon=True).start()

    def stop(self) -> None:
        self.running = False
        for d in list(self.dialogs.values()):
            self.hangup(d)
        for s in (self.sock, self.psock):
            try:
                s and s.close()
            except OSError:
                pass

    def _send(self, msg: Msg, addr, sock=None) -> None:
        log(f"out {addr[0]}:{addr[1]} {describe(msg)}")
        try:
            (sock or getattr(self.tl, "sock", None) or self.sock).sendto(msg.encode(), addr)
        except OSError as exc:
            log(f"send to {addr[0]}:{addr[1]} failed: {exc}")
            sys.stderr.write(f"jarvis sip: send failed: {exc}\n")

    def _loop(self, sock) -> None:
        self.tl.sock = sock
        sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data.strip():
                continue  # a keep-alive
            msg = Msg.parse(data)
            if not msg:
                log(f"in  {addr[0]}:{addr[1]} unreadable packet, {len(data)} bytes: {data[:60]!r}")
                continue
            log(f"in  {addr[0]}:{addr[1]} {describe(msg)}")
            try:
                self._handle(msg, addr)
            except Exception as exc:  # one odd packet must never stop the phone line
                log(f"error handling {msg.first}: {type(exc).__name__}: {exc}")
                sys.stderr.write(f"jarvis sip: {type(exc).__name__}: {exc}\n")

    def _local(self, sock) -> tuple[str, int]:
        """The address to put in Via and Contact for messages on this socket."""
        if sock is self.psock and sock is not None:
            return self.preg.get("public") or (self.ip, sock.getsockname()[1])
        return self.ip, self.port

    def _via(self, sock) -> str:
        ip, port = self._local(sock)
        transport = self.ptransport if sock is self.psock and sock is not None else "UDP"
        return f"SIP/2.0/{transport} {ip}:{port};branch=z9hG4bK{secrets.token_hex(8)};rport"

    def _reply(self, req: Msg, addr, code: int, reason: str, extra: list | None = None, body: str = "",
               to_tag: str = "") -> None:
        headers = []
        for v in req.all("via"):
            if not headers:  # the top Via: say where it really came from (rport), so NAT can't lose the reply
                v = re.sub(r";rport(?=;|$)", f";rport={addr[1]}", v)
                if "received=" not in v:
                    v += f";received={addr[0]}"
            headers.append(("via", v))
        for rr in req.all("record-route"):
            headers.append(("record-route", rr))
        to = req.get("to")
        if code >= 200 and not to_tag:  # RFC 3261 8.2.6.2: final responses carry a To tag; Linphone drops them otherwise
            to_tag = secrets.token_hex(4)
        if to_tag and not tag_of(to):
            to += f";tag={to_tag}"
        headers += [("from", req.get("from")), ("to", to), ("call-id", req.get("call-id")), ("cseq", req.get("cseq"))]
        headers += extra or []
        headers.append(("server", "JARVIS"))
        if body:
            headers.append(("content-type", "application/sdp"))
        self._send(Msg(f"SIP/2.0 {code} {reason}", headers, body), addr)

    # auth, as the server
    def _challenge(self, req: Msg, addr) -> None:
        nonce = secrets.token_hex(16)
        self.nonces[nonce] = time.time()
        for n in [n for n, t in self.nonces.items() if time.time() - t > 300]:
            del self.nonces[n]
        self._reply(req, addr, 401, "Unauthorized",
                    [("www-authenticate", f'Digest realm="{REALM}", nonce="{nonce}", algorithm=MD5, qop="auth"')])

    def _authorized(self, req: Msg, addr=None) -> bool:
        value = req.get("authorization") or req.get("proxy-authorization")
        if not value and addr and self._trusted(addr[0]):
            log(f"signed in without a password: {addr[0]} is on the tailnet")
            return True
        if not value.lower().startswith("digest"):
            return False
        p = auth_params(value[6:])
        if p.get("username") != USER:
            log(f"sign-in refused: username {p.get('username')!r}, the account is {USER!r}")
            return False
        if p.get("nonce") not in self.nonces:
            log("sign-in refused: stale nonce, challenging again")
            return False
        want = digest(USER, self.pw, req.method, p.get("uri", ""), p["nonce"], p.get("nc", ""), p.get("cnonce", ""),
                      p.get("qop", ""))
        if not hmac.compare_digest(want, p.get("response", "")):
            log("sign-in refused: wrong password (digest mismatch)")
            return False
        return True

    def _trusted(self, ip: str) -> bool:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in n for n in self.trusted)

    # auth, as the provider's client
    def _answer_challenge(self, challenge: Msg, method: str, uri: str) -> tuple[str, str]:
        """(header name, value) answering a provider's 401/407, with MD5 or SHA-256 as it asks. With a stored
        HA1, the challenge in its algorithm is the one answered (sip.linphone.org may offer both)."""
        name = "proxy-authorization" if challenge.status == 407 else "authorization"
        offers = [auth_params(v[6:]) for v in challenge.all("proxy-authenticate" if challenge.status == 407
                                                             else "www-authenticate") if v.lower().startswith("digest")]
        key = self.provider_ha1
        algo_of = lambda q: (q.get("algorithm") or "MD5").upper()  # noqa: E731
        p = next((q for q in offers if key and algo_of(q) == key.get("algorithm", "MD5").upper()), offers[0] if offers else {})
        algo = algo_of(p)
        h = (lambda s: hashlib.sha256(s.encode()).hexdigest()) if algo == "SHA-256" else \
            (lambda s: hashlib.md5(s.encode()).hexdigest())
        user, realm, nonce = self.provider["user"], p.get("realm", ""), p.get("nonce", "")
        if key and algo == key.get("algorithm", "MD5").upper() and key.get("realm", realm) == realm:
            ha1 = key["ha1"]
        else:
            ha1 = h(f"{user}:{realm}:{self.provider_pw}")
        ha2 = h(f"{method}:{uri}")
        if "auth" in (p.get("qop") or "").split(","):
            cnonce, nc = secrets.token_hex(8), "00000001"
            resp = h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
            extra = f', qop=auth, nc={nc}, cnonce="{cnonce}"'
        else:
            resp, extra = h(f"{ha1}:{nonce}:{ha2}"), ""
        value = (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}", '
                 f'algorithm={algo}{extra}' + (f', opaque="{p["opaque"]}"' if p.get("opaque") else ""))
        return name, value

    # requests from the phone
    def _handle(self, msg: Msg, addr) -> None:
        if not msg.is_request:
            return self._response(msg, addr)
        method = msg.method
        if method == "REGISTER":
            return self._register(msg, addr)
        if method == "OPTIONS":
            return self._reply(msg, addr, 200, "OK", [("allow", "INVITE, ACK, BYE, CANCEL, OPTIONS")])
        if method == "INVITE":
            return self._incoming(msg, addr)
        d = self.dialogs.get(msg.get("call-id"))
        if method == "ACK":
            if d and d.incoming and not d.answered.is_set():
                d.answered.set()
                self._start_leg(d)
            return
        if method == "BYE":
            self._reply(msg, addr, 200, "OK")
            if d:
                self._end(d, "hung up")
            return
        if method == "CANCEL":
            self._reply(msg, addr, 200, "OK")
            if d and d.invite:
                self._reply(d.invite, d.addr, 487, "Request Terminated", to_tag=d.local_tag)
                self._end(d, "cancelled")
            return
        if method in ("UPDATE", "INFO", "NOTIFY"):
            return self._reply(msg, addr, 200, "OK")
        self._reply(msg, addr, 405, "Method Not Allowed")

    def _register(self, req: Msg, addr) -> None:
        if getattr(self.tl, "sock", None) is self.psock and self.psock is not None:
            return self._reply(req, addr, 403, "Forbidden")  # nobody registers to JARVIS from the internet
        if not self._authorized(req, addr):
            return self._challenge(req, addr)
        contact = req.get("contact")
        m = re.search(r"expires=(\d+)", contact)
        expires = int(m.group(1)) if m else int(req.get("expires") or 3600)
        if expires == 0 or contact.strip() == "*":
            self.contact = {}
            return self._reply(req, addr, 200, "OK")
        self.contact = {"uri": uri_in(contact), "addr": addr, "expires": time.time() + expires}
        self.last_registered = time.time()
        self._reply(req, addr, 200, "OK", [("contact", f"<{self.contact['uri']}>;expires={expires}"),
                                            ("expires", str(expires))])

    def registered(self) -> bool:
        return bool(self.contact) and self.contact["expires"] > time.time()

    def _incoming(self, req: Msg, addr) -> None:
        """The owner rings JARVIS from the app (on the tailnet, signed in; or the provider's account, from the
        owner's own address only)."""
        cid = req.get("call-id")
        d = self.dialogs.get(cid)
        sock = getattr(self.tl, "sock", None) or self.sock
        if d and not d.incoming and not tag_of(req.get("to")):  # JARVIS's own ring forked back to him
            return self._reply(req, addr, 482, "Loop Detected")
        if d:  # a retransmission, or a re-INVITE refreshing the call: same answer
            if d.leg and tag_of(req.get("to")):
                self._reply(req, addr, 200, "OK", [("contact", self._contact_header(sock))],
                            sdp(self._media_ip(sock), d.rtp.port), to_tag=d.local_tag)
            return
        if sock is self.psock and self.psock is not None:
            if uri_in(req.get("from")).split(";")[0] != self.provider.get("callee"):
                return self._reply(req, addr, 403, "Forbidden")  # only the owner may ring JARVIS this way
            if self.instance:
                log(f"the owner is ringing JARVIS's line ({'his own address' if self._for_line(req) else 'the account'})")
        elif not self._authorized(req, addr):
            return self._challenge(req, addr)
        ip, port, pcmu = sdp_media(req.body)
        if not pcmu:
            return self._reply(req, addr, 488, "Not Acceptable Here")
        if any(x.leg for x in self.dialogs.values()):
            return self._reply(req, addr, 486, "Busy Here")
        d = Dialog(cid, secrets.token_hex(4), uri_in(req.get("contact")) or uri_in(req.get("from")), addr,
                   tag_of(req.get("from")), incoming=True, sock=sock)
        d.invite = req
        d.rtp = self._rtp(sock)
        d.rtp.remote = (ip, port)
        self.dialogs[cid] = d
        self._reply(req, addr, 100, "Trying")
        self._reply(req, addr, 200, "OK", [("contact", self._contact_header(sock))],
                    sdp(self._media_ip(sock), self._media_port(d.rtp)), to_tag=d.local_tag)

    def _for_line(self, req: Msg) -> bool:
        """Dialled at JARVIS's own address (gr=<his instance>), not just the shared account."""
        uuid = self.instance.rsplit(":", 1)[-1].lower()
        return bool(uuid) and uuid in (req.get("to") + " " + req.uri).lower()

    def _contact_header(self, sock) -> str:
        ip, port = self._local(sock)
        if sock is self.psock and self.psock is not None:
            transport = "" if self.ptransport == "UDP" else f";transport={self.ptransport.lower()}"
            return f"<sip:{self.provider.get('user')}@{ip}:{port}{transport}>"
        return f"<sip:{JARVIS_USER}@{ip}:{port}>"

    def _rtp(self, sock) -> Rtp:
        provider = sock is self.psock and self.psock is not None
        rtp = Rtp("0.0.0.0" if provider else self.bind, self.rtp_port, lambda _: None)
        if provider and self.provider.get("media") == "stun":
            rtp.public = stun_address(rtp.sock)
        return rtp

    def _media_ip(self, sock) -> str:
        if sock is self.psock and self.psock is not None and self.provider.get("media") == "stun":
            return self.preg.get("public", ("",))[0] or self.ip
        return self.ip

    @staticmethod
    def _media_port(rtp: Rtp) -> int:
        public = getattr(rtp, "public", None)
        return public[1] if public and public[1] else rtp.port

    # JARVIS rings the phone
    def ring(self, wait: bool = True, ring_for: float = RING_FOR) -> str:
        """Ring the owner: through the provider (push: rings an iPhone even with the app shut) if one is set up,
        else the softphone registered over Tailscale. "answered", "declined", "no answer", "no phone", "busy"."""
        if any(d.leg for d in self.dialogs.values()):
            return "busy"
        if self.psock is not None and self.provider.get("callee"):
            sock, addr, target = self.psock, self.paddr, self.provider["callee"]
            frm_user, domain = self.provider["user"], self.provider["server"]
        elif self.registered():
            sock, addr, target = self.sock, self.contact["addr"], self.contact["uri"]
            frm_user, domain = JARVIS_USER, self.ip
        else:
            return "no phone"
        cid = secrets.token_hex(12) + "@jarvis"
        d = Dialog(cid, secrets.token_hex(4), target, addr, sock=sock)
        d.rtp = self._rtp(sock)
        to = f"<{target}>" if sock is self.psock else f"<sip:{USER}@{self.ip}>"
        d.invite = Msg(f"INVITE {target} SIP/2.0", [
            ("via", self._via(sock)),
            ("max-forwards", "70"),
            ("from", f"\"JARVIS\" <sip:{frm_user}@{domain}>;tag={d.local_tag}"),
            ("to", to), ("call-id", cid), ("cseq", "1 INVITE"),
            ("contact", self._contact_header(sock)),
            ("allow", "INVITE, ACK, BYE, CANCEL, OPTIONS"),
            ("user-agent", "JARVIS"), ("content-type", "application/sdp")],
            sdp(self._media_ip(sock), self._media_port(d.rtp)))
        self.dialogs[cid] = d
        threading.Thread(target=self._retransmit, args=(d,), daemon=True).start()
        if not wait:
            threading.Thread(target=self._await, args=(d, ring_for), daemon=True).start()
            return "ringing"
        return self._await(d, ring_for)

    def _retransmit(self, d: Dialog) -> None:
        delay, sent = 0.5, None
        while not d.provisional.is_set() and not d.answered.is_set() and not d.result and self.running:
            if sent is not d.invite:  # a new INVITE (answering a challenge) starts the timer again
                sent, delay = d.invite, 0.5
            elif isinstance(d.sock, StreamSock):  # TCP/TLS delivers it: no resending
                d.provisional.wait(delay)
                continue
            self._send(d.invite, d.addr, d.sock)
            d.provisional.wait(delay)
            delay = min(delay * 2, 4.0)

    def _await(self, d: Dialog, ring_for: float) -> str:
        if not d.answered.wait(ring_for) and not d.result:
            self._cancel(d)
            self._end(d, "no answer")
        return d.result or "answered"

    def _cancel(self, d: Dialog) -> None:
        self._send(Msg(f"CANCEL {d.remote_uri} SIP/2.0", [
            ("via", d.invite.get("via")), ("max-forwards", "70"), ("from", d.invite.get("from")),
            ("to", d.invite.get("to")), ("call-id", d.call_id), ("cseq", d.invite.get("cseq").replace("INVITE", "CANCEL"))]
            + [("route", r) for r in d.routes]), d.addr, d.sock)

    def _response(self, msg: Msg, addr) -> None:
        if msg.get("call-id") == self.preg["call_id"]:
            return self._provider_registered(msg)
        d = self.dialogs.get(msg.get("call-id"))
        if not d or d.incoming:
            return
        code, method = msg.status, msg.cseq_method
        if method != "INVITE":
            return
        if 100 <= code < 200:
            d.provisional.set()
            return
        if code in (401, 407) and d.sock is self.psock and "authorization" not in str(d.invite.headers).lower():
            self._ack_failure(d, msg, addr)
            name, value = self._answer_challenge(msg, "INVITE", d.remote_uri)
            headers = [(k, v) for k, v in d.invite.headers if k not in ("via", "cseq")]
            d.cseq += 1
            d.invite = Msg(d.invite.first, [("via", self._via(d.sock)),
                                            ("cseq", f"{d.cseq} INVITE"), *headers, (name, value)], d.invite.body)
            self._send(d.invite, d.addr, d.sock)
            return
        d.provisional.set()
        if 200 <= code < 300:
            if d.answered.is_set():  # a retransmitted 200: ACK it again
                return self._ack(d, msg, addr)
            d.remote_tag = tag_of(msg.get("to"))
            d.remote_uri = uri_in(msg.get("contact")) or d.remote_uri
            d.routes = list(reversed(msg.all("record-route")))
            d.to = msg.get("to")
            ip, port, _ = sdp_media(msg.body)
            d.rtp.remote = (ip, port) if ip and port else None
            self._ack(d, msg, addr)
            d.answered.set()
            self._start_leg(d)
            return
        self._ack_failure(d, msg, addr)
        self._end(d, "declined" if code in (486, 600, 603) else f"failed ({code})")

    def _ack_failure(self, d: Dialog, msg: Msg, addr) -> None:
        """ACK a final non-2xx answer, as SIP requires (same branch as the INVITE)."""
        self._send(Msg(f"ACK {d.invite.first.split(' ')[1]} SIP/2.0", [
            ("via", d.invite.get("via")), ("max-forwards", "70"), ("from", d.invite.get("from")),
            ("to", msg.get("to")), ("call-id", d.call_id),
            ("cseq", d.invite.get("cseq").replace("INVITE", "ACK"))]), addr, d.sock)

    def _in_dialog(self, d: Dialog, method: str, cseq: int) -> Msg:
        if d.incoming:
            frm = d.invite.get("to") + ("" if tag_of(d.invite.get("to")) else f";tag={d.local_tag}")
            to = d.invite.get("from")
            routes = d.invite.all("record-route")
        else:
            frm, to, routes = d.invite.get("from"), getattr(d, "to", "") or f"<{d.remote_uri}>;tag={d.remote_tag}", d.routes
        return Msg(f"{method} {d.remote_uri} SIP/2.0", [
            ("via", self._via(d.sock)),
            ("max-forwards", "70"), *[("route", r) for r in routes], ("from", frm), ("to", to),
            ("call-id", d.call_id), ("cseq", f"{cseq} {method}")])

    def _ack(self, d: Dialog, ok: Msg, addr) -> None:
        self._send(self._in_dialog(d, "ACK", d.cseq), addr, d.sock)

    # the provider account (push ringing for an iPhone)
    def _provider_register(self, auth: tuple[str, str] | None = None) -> None:
        user, server = self.provider["user"], self.provider["server"]
        self.preg["cseq"] += 1
        headers = [("via", self._via(self.psock)),
                   ("max-forwards", "70"), ("from", f"<sip:{user}@{server}>;tag={self.preg['tag']}"),
                   ("to", f"<sip:{user}@{server}>"), ("call-id", self.preg["call_id"]),
                   ("cseq", f"{self.preg['cseq']} REGISTER"), ("user-agent", "JARVIS")]
        if self.instance:  # JARVIS's own device on the account: the provider gives it an address of its own
            headers += [("supported", "gruu, outbound, path"),
                        ("contact", f'{self._contact_header(self.psock)};+sip.instance="<{self.instance}>"'),
                        ("expires", str(LINE_EXPIRES))]
        elif not self.same_account:  # with no Contact, a REGISTER only signs in and lists the account's devices
            headers += [("contact", self._contact_header(self.psock)), ("expires", "600")]
        if auth:
            headers.append(auth)
        self._send(Msg(f"REGISTER sip:{server} SIP/2.0", headers), self.paddr, self.psock)

    def _provider_registered(self, msg: Msg) -> None:
        if msg.status in (401, 407) and not self.preg.get("tried"):
            self.preg["tried"] = True
            return self._provider_register(self._answer_challenge(msg, "REGISTER", f"sip:{self.provider['server']}"))
        self.preg["tried"] = False
        if 200 <= msg.status < 300:
            contacts = [c for v in msg.all("contact") for c in re.findall(r"<[^>]+>", v)]
            self.preg.update(ok=True, error="", at=time.time(), devices=len(contacts),
                             push=sum("pn-provider=" in c for c in contacts))
            m = re.search(r"received=([\d.]+)", msg.get("via"))
            r = re.search(r"rport=(\d+)", msg.get("via"))
            if m and r:
                self.preg["public"] = (m.group(1), int(r.group(1)))  # how the internet sees this socket
            if self.instance:
                self._line_registered(msg)
        elif msg.status >= 300:
            self.preg.update(ok=False, error=f"{msg.status} {msg.first.split(' ', 2)[-1]}")

    def _line_registered(self, msg: Msg) -> None:
        """The provider's answer to JARVIS's own device: its address (pub-gruu) and how long the binding lasts."""
        mine = next((v for v in msg.all("contact") if self.instance.lower() in v.lower()), "")
        if not mine:
            self.preg.update(ok=False, error="the provider kept no binding for JARVIS's line")
            return
        m = re.search(r'pub-gruu="([^"]+)"', mine)
        e = re.search(r"expires=(\d+)", mine) or re.search(r"(\d+)", msg.get("expires"))
        self.preg.update(gruu=m.group(1) if m else "", expires=int(e.group(1)) if e else LINE_EXPIRES)
        if int(self.preg["expires"]) == 0:
            self.preg.update(ok=False, error="the provider dropped JARVIS's line")

    def _renew_after(self) -> float:
        """Seconds between sign-ins: half the line's binding (it must never lapse), else every five minutes, and
        soon after a failure."""
        if not self.preg["ok"]:
            return RETRY_AFTER
        if self.instance:
            return max(RETRY_AFTER, min(300.0, int(self.preg.get("expires") or LINE_EXPIRES) / 2))
        return 300.0

    def _connects(self) -> int:
        return getattr(self.psock, "connects", 0)  # TCP/TLS only: a UDP socket has no connection to lose

    def _provider_keepalive(self) -> None:
        last, connects = 0.0, self._connects()
        while self.running and self.psock is not None:
            now_connects = self._connects()
            if time.time() - last > self._renew_after() or (self.instance and now_connects != connects):
                if now_connects != connects:
                    log("provider connection is new: signing JARVIS's line in again")
                connects = now_connects
                self._provider_register()
                last = time.time()
            else:
                try:
                    self.psock.sendto(b"\r\n\r\n", self.paddr)  # keeps the home router's door open
                except OSError:
                    pass
            time.sleep(self.KEEPALIVE)

    # the call itself
    def _start_leg(self, d: Dialog) -> None:
        d.result = "answered"
        d.rtp.on_dead = lambda: self.hangup(d)
        d.rtp.start()
        d.leg = PhoneLeg(self.call, d.rtp)
        d.leg.start()

    def hangup(self, d: Dialog) -> None:
        """JARVIS ends the call."""
        if d.leg:
            d.cseq += 1
            self._send(self._in_dialog(d, "BYE", d.cseq), d.addr, d.sock)
        self._end(d, "hung up")

    def _end(self, d: Dialog, result: str) -> None:
        d.result = d.result or result
        d.answered.set()  # wakes a ring still waiting for an answer
        if d.leg:
            d.leg.stop()
            d.leg = None
        elif d.rtp:
            d.rtp.stop()
        self.dialogs.pop(d.call_id, None)

    def status(self) -> dict:
        out = {"registered": self.registered(), "on_call": any(d.leg for d in self.dialogs.values()),
               "address": f"{self.ip}:{self.port}", "user": USER}
        if self.provider.get("server"):
            out["provider"] = {"server": self.provider["server"], "user": self.provider.get("user"),
                               "registered": self.preg["ok"], "error": self.preg["error"],
                               "auth": "ha1" if self.provider_ha1 else "password",
                               "same_account": self.same_account, "devices": self.preg.get("devices", 0),
                               "push_devices": self.preg.get("push", 0)}
            if self.instance:
                fresh = time.time() - self.preg.get("at", 0) < int(self.preg.get("expires") or LINE_EXPIRES)
                out["provider"]["line"] = {"on": self.preg["ok"] and bool(self.preg.get("gruu")) and fresh,
                               "up_for": round(time.time() - self.started),
                               "address": self.preg.get("gruu") or line_address(self.provider),
                               "renews_in": max(0, round(self.preg.get("at", 0) + self._renew_after() - time.time()))}
        return out


def setup_text(bind: str = "") -> str:
    """The owner's steps to make the phone ring, and the account to type into the app."""
    ip = bind or tailscale_ip() or "<this PC's Tailscale address, 100.x.y.z>"
    s = settings()
    return f"""JARVIS phone line (SIP): the phone rings like a real call. No number, no SIM, nothing paid.

A. Over Tailscale (works now; on an iPhone it rings only while Linphone is open or was just used)
  On the PC (once):
    1. Tailscale on the PC and phone, same account (done if both show in the Tailscale app).
    2. bin/jarvis call sip --bind {ip}    then restart the call server.
    3. Windows asks to let Python through the firewall: tick Private networks and Allow (the Tailscale
       network is Private). If it never asks: run scripts/allow-jarvis-phone-line.ps1 as administrator.
  On the phone (once):
    4. Install Linphone (free, App Store / Play Store). Choose "Use a SIP account" (third-party SIP):
         Username:  {USER}
         Password:  (bin/jarvis call sip --show-password, on the PC)
         Domain:    {ip}:{s['port']}
         Transport: UDP
    5. Android: Linphone settings > keep alive service ON; let it ignore battery optimisation.

B. iPhone that rings even with the app shut (push), still free
    Quickest: the Linphone desktop app on this PC signed in to the iPhone's own sip.linphone.org account, then
       bin/jarvis call sip --from-linphone    and restart the call server. JARVIS uses that app's HA1 (no
       password needed), signs in over TLS without adding a device, and rings the account: the provider pushes
       the iPhone awake. Or, with two accounts:
    1. In Linphone create TWO free sip.linphone.org accounts (Linphone's own "Create an account"; email,
       no phone number): one for the owner's iPhone (sign the iPhone into it, push notifications ON), one for
       JARVIS (don't sign the phone into this one).
    2. Put JARVIS's account password in brain/secrets/sip_provider_password (a file on the PC, never in chat).
    3. bin/jarvis call sip --provider-user <JARVIS's account name> --callee <owner's account name>
       then restart the call server. JARVIS signs in to sip.linphone.org and rings the owner's account;
       Linphone's push service wakes the iPhone and shows the normal incoming-call screen. The voice itself
       still runs over Tailscale, so keep Tailscale on.

C. The owner rings JARVIS from Linphone (after B's quickest way)
    bin/jarvis call sip --line    then restart the call server (the routines tick does it within 15 minutes).
       JARVIS signs in as his own device on the same account, kept signed in 24/7, and the provider gives that
       device an address of its own. In Linphone add a contact "JARVIS" with that SIP address:
         {line_address(s['provider']) or '(shown here once --line is set)'}
       Tapping it rings JARVIS alone, never the owner's own phone, and he answers.

Test either way: bin/jarvis call ring --say 'Testing the phone line, sir.'
"""
