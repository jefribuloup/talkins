"""Pengambilan file dari link yang dimasukkan pengguna, aman dari SSRF.

Masalah yang ditutup:
- SSRF: server tidak boleh dipakai untuk mengakses localhost, jaringan privat, atau alamat metadata
  cloud (169.254.169.254, dll.).
- DNS rebinding: nama host diperiksa SEBELUM terhubung dan IP tujuan yang benar-benar tersambung
  diperiksa lagi di level socket, jadi hasil DNS yang berganti di tengah jalan tetap ditolak.
- Redirect: diikuti manual (maks. MAX_REDIRECTS) dan SETIAP hop diperiksa ulang.
- Unduhan tanpa batas: dibaca bertahap dengan batas byte (setelah dekompresi, jadi gzip bomb
  tertahan), plus batas waktu total.

Hanya skema http/https, tanpa user:password di URL, port 80/443. Env: URL_MAX_MB (default 20).
Pesan galat berupa ValueError berbahasa Indonesia yang aman ditampilkan ke pengguna.
"""
import ipaddress
import os
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

ALLOWED_PORTS = (80, 443)
VERIFY = True  # verifikasi sertifikat TLS (hanya diubah oleh tes)
MAX_REDIRECTS = 5
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15
TOTAL_TIMEOUT = 25  # detik, untuk seluruh proses unduh termasuk redirect

BLOCKED_MSG = ("Link ini tidak bisa dipakai. Gunakan link publik (http/https) ke file .xlsx "
               "atau Google Sheets, bukan alamat internal/pribadi.")
FAIL_MSG = ("Gagal mengambil data dari link. Pastikan link bisa diakses publik "
            "(Google Sheets: Share -> Anyone with the link -> Viewer).")

_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".localdomain", ".lan", ".home.arpa", ".intranet")
# Rentang IPv6 yang bisa menyelundupkan IPv4 internal (NAT64 / 6to4) -> ditolak
_EXTRA_BLOCKED = tuple(ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16"))

_tl = threading.local()  # penanda: koneksi ditolak karena IP tujuan tidak diizinkan


def url_max_mb():
    """Batas ukuran file dari link (MB). Atur lewat env URL_MAX_MB; default 20."""
    try:
        return max(1, int(os.environ.get("URL_MAX_MB", "20")))
    except ValueError:
        return 20


def _ip_allowed(ip_text):
    """True hanya untuk alamat IP publik (global, bukan multicast)."""
    try:
        ip = ipaddress.ip_address(str(ip_text).split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in net for net in _EXTRA_BLOCKED if ip.version == net.version):
        return False
    return bool(ip.is_global) and not ip.is_multicast


def _check_host(host, port):
    """Tolak host yang menunjuk ke alamat non-publik. Semua hasil DNS harus publik."""
    low = host.lower().rstrip(".")
    if not low or low == "localhost" or low.endswith(_BLOCKED_SUFFIXES):
        raise ValueError(BLOCKED_MSG)
    try:
        literal = ipaddress.ip_address(low.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not _ip_allowed(literal):
            raise ValueError(BLOCKED_MSG)
        return
    if "." not in low:  # nama tanpa titik = nama jaringan internal (mis. "metadata")
        raise ValueError(BLOCKED_MSG)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError(FAIL_MSG)
    if not infos or not all(_ip_allowed(i[4][0]) for i in infos):
        raise ValueError(BLOCKED_MSG)


def _validate_url(url):
    try:
        sp = urlsplit(url)
        port = sp.port
    except ValueError:
        raise ValueError("Link tidak valid.")
    if sp.scheme not in ("http", "https") or not sp.hostname:
        raise ValueError("Link tidak valid. Gunakan alamat yang diawali http:// atau https://")
    if sp.username or sp.password:
        raise ValueError(BLOCKED_MSG)
    if (port or (443 if sp.scheme == "https" else 80)) not in ALLOWED_PORTS:
        raise ValueError(BLOCKED_MSG)
    _check_host(sp.hostname, port or (443 if sp.scheme == "https" else 80))


# ---- Pemeriksaan di level socket: IP yang benar-benar tersambung harus publik ----

class _Guard:
    def _new_conn(self):
        sock = super()._new_conn()
        try:
            peer = sock.getpeername()[0]
        except OSError:
            peer = ""
        if not _ip_allowed(peer):
            _tl.blocked = True
            sock.close()
            raise OSError("tujuan koneksi tidak diizinkan")
        return sock


class _GuardedHTTPConnection(_Guard, HTTPConnection):
    pass


class _GuardedHTTPSConnection(_Guard, HTTPSConnection):
    pass


class _GuardedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class _GuardedAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _GuardedHTTPPool, "https": _GuardedHTTPSPool}


def fetch_public_url(url, max_bytes=None):
    """Unduh isi `url` (maks. max_bytes). Return (bytes, url_akhir). Raise ValueError berpesan ramah."""
    max_bytes = max_bytes or url_max_mb() * 1024 * 1024
    too_big = f"File dari link terlalu besar (maksimal {max_bytes // (1024 * 1024) or 1} MB)."
    deadline = time.monotonic() + TOTAL_TIMEOUT

    sess = requests.Session()
    sess.trust_env = False            # jangan ikut proxy/kredensial dari environment
    sess.verify = VERIFY
    adapter = _GuardedAdapter()
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    sess.headers["User-Agent"] = "Talkins/1.0 (impor file)"

    current = str(url).strip()
    resp = None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _validate_url(current)
            _tl.blocked = False
            try:
                resp = sess.get(current, stream=True, allow_redirects=False,
                                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            except requests.RequestException:
                raise ValueError(BLOCKED_MSG if getattr(_tl, "blocked", False) else FAIL_MSG)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                resp.close()
                if not loc:
                    raise ValueError(FAIL_MSG)
                current = urljoin(current, loc)
                continue
            break
        else:
            raise ValueError("Link dialihkan terlalu banyak kali.")

        if not resp.ok:
            raise ValueError(FAIL_MSG)
        declared = resp.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise ValueError(too_big)

        chunks, total = [], 0
        try:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                total += len(chunk)
                if total > max_bytes:   # dihitung setelah dekompresi -> gzip bomb tertahan
                    raise ValueError(too_big)
                if time.monotonic() > deadline:
                    raise ValueError("Unduhan dari link terlalu lama. Coba lagi atau unggah file langsung.")
                chunks.append(chunk)
        except requests.RequestException:
            raise ValueError("Unduhan dari link terputus. Coba lagi sebentar lagi.")
        return b"".join(chunks), current
    finally:
        if resp is not None:
            resp.close()
        sess.close()
