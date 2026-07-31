from __future__ import annotations
# ============================================================
#  local_auth_proxy.py
#  Local HTTP proxy (127.0.0.1) that injects Proxy-Authorization
#  toward an upstream residential proxy.
#
#  Chrome → 127.0.0.1:PORT (no login popup)
#         → upstream host:port with Basic auth
#
#  Fixes VPS + local: Chrome will not ask for proxy credentials.
# ============================================================
import base64
import logging
import select
import socket
import threading
from urllib.parse import urlparse

logger = logging.getLogger("AgentScraper")


class LocalAuthProxy:
    """Threaded localhost forwarder with upstream proxy auth."""

    def __init__(
        self,
        upstream_host: str,
        upstream_port: int,
        username: str = "",
        password: str = "",
        listen_host: str = "127.0.0.1",
    ) -> None:
        self.upstream_host = upstream_host
        self.upstream_port = int(upstream_port)
        self.username = username or ""
        self.password = password or ""
        self.listen_host = listen_host
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port: int | None = None

    def _auth_header(self) -> str:
        token = base64.b64encode(
            f"{self.username}:{self.password}".encode("utf-8")
        ).decode("ascii")
        return f"Proxy-Authorization: Basic {token}\r\n"

    def start(self) -> int:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.listen_host, 0))
        self._sock.listen(64)
        self._sock.settimeout(1.0)
        self.port = int(self._sock.getsockname()[1])
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve,
            name="realtor-local-auth-proxy",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "🔐  Local auth proxy on %s:%s → %s:%s (Chrome needs no password popup)",
            self.listen_host,
            self.port,
            self.upstream_host,
            self.upstream_port,
        )
        return self.port

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        logger.info("🛑  Local auth proxy stopped")

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_client,
                args=(client,),
                daemon=True,
            ).start()

    def _handle_client(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(30)
            raw = b""
            while b"\r\n\r\n" not in raw and len(raw) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    break
                raw += chunk
            if not raw:
                return

            header_blob, _, rest = raw.partition(b"\r\n\r\n")
            lines = header_blob.decode("latin-1", errors="replace").split("\r\n")
            if not lines or not lines[0]:
                return
            first = lines[0]
            parts = first.split(" ")
            if len(parts) < 2:
                return
            method = parts[0].upper()

            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port),
                timeout=30,
            )
            upstream.settimeout(30)
            auth = self._auth_header() if self.username else ""

            if method == "CONNECT":
                # Tunnel HTTPS through upstream proxy
                connect_req = (
                    f"{first}\r\n"
                    f"Host: {parts[1]}\r\n"
                    f"{auth}"
                    f"Proxy-Connection: keep-alive\r\n"
                    f"\r\n"
                ).encode("latin-1")
                upstream.sendall(connect_req)
                resp = b""
                while b"\r\n\r\n" not in resp and len(resp) < 65536:
                    chunk = upstream.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                status_line = resp.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
                if " 200 " not in status_line and not status_line.endswith(" 200"):
                    # Pass upstream error to Chrome so logs stay useful
                    try:
                        client.sendall(resp if resp else b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    except Exception:
                        pass
                    return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
                self._pipe(client, upstream)
                return

            # Plain HTTP request via upstream proxy
            filtered = [lines[0]]
            for line in lines[1:]:
                low = line.lower()
                if low.startswith("proxy-authorization:"):
                    continue
                if low.startswith("proxy-connection:"):
                    continue
                filtered.append(line)
            filtered.append("Proxy-Connection: keep-alive")
            if auth:
                filtered.append(auth.strip())
            new_headers = ("\r\n".join(filtered) + "\r\n\r\n").encode("latin-1")
            upstream.sendall(new_headers + rest)
            self._pipe(client, upstream)
        except Exception as e:
            logger.debug("Local auth proxy client error: %s", e)
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            except Exception:
                pass
        finally:
            for s in (client, upstream):
                if s is None:
                    continue
                try:
                    s.close()
                except Exception:
                    pass

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        sockets = [a, b]
        try:
            while True:
                readable, _, errored = select.select(sockets, [], sockets, 60)
                if errored or not readable:
                    break
                for sock in readable:
                    other = b if sock is a else a
                    data = sock.recv(65536)
                    if not data:
                        return
                    other.sendall(data)
        except Exception:
            return


def start_local_auth_proxy_from_parts(parts: dict) -> LocalAuthProxy | None:
    """
    If proxy has username/password, start localhost forwarder.
    Returns the running LocalAuthProxy, or None if no auth needed.
    """
    if not parts or not parts.get("has_auth"):
        return None
    host = parts.get("host") or ""
    port = parts.get("port")
    if not host or not port:
        return None
    fwd = LocalAuthProxy(
        upstream_host=host,
        upstream_port=int(port),
        username=parts.get("username") or "",
        password=parts.get("password") or "",
    )
    fwd.start()
    return fwd


def chrome_proxy_server_arg(parts: dict, local_fwd: LocalAuthProxy | None) -> str:
    """Value for --proxy-server=… (never includes user:pass)."""
    if local_fwd and local_fwd.port:
        return f"http://127.0.0.1:{local_fwd.port}"
    return (parts.get("server") or "").strip()
