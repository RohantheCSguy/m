from http.server import BaseHTTPRequestHandler
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlencode
import ipaddress
import json
import socket

MAX_BYTES = 8 * 1024 * 1024


def validate_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a valid HTTP or HTTPS server URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter only the server address, without credentials, query or fragment.")
    if parsed.path not in ("", "/"):
        raise ValueError("Enter the base server URL only, without a path.")
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError("Private or local network addresses are not allowed.")
        return parsed
    except ValueError as exc:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise exc
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        raise ValueError("The IPTV server hostname could not be resolved.")
    if not addresses:
        raise ValueError("The IPTV server hostname could not be resolved.")
    for item in addresses:
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError("The IPTV server resolves to a private or local address.")
    return parsed


class SameHostRedirectHandler(HTTPRedirectHandler):
    def __init__(self, original_host):
        super().__init__()
        self.original_host = original_host.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = validate_public_url(newurl)
        if parsed.hostname.lower() != self.original_host:
            raise ValueError("The provider redirected to a different host; redirect blocked to protect credentials.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/xtream":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError("Invalid request size.")
            payload = json.loads(self.rfile.read(length))
            host = payload.get("host", "")
            username = payload.get("username", "")
            password = payload.get("password", "")
            if not all(isinstance(v, str) and v.strip() for v in (host, username, password)):
                raise ValueError("Server URL, username and password are required.")
            if len(host) > 2048 or len(username) > 512 or len(password) > 1024:
                raise ValueError("One or more fields are too long.")
            parsed = validate_public_url(host.strip().rstrip("/"))
            base = f"{parsed.scheme}://{parsed.netloc}"
            query = urlencode({"username": username, "password": password, "type": "m3u_plus", "output": "ts"})
            playlist_url = base + "/get.php?" + query
            request = Request(playlist_url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; M3U-Manager/1.0)",
                "Accept": "audio/x-mpegurl, application/vnd.apple.mpegurl, text/plain, */*",
            })
            opener = build_opener(SameHostRedirectHandler(parsed.hostname))
            with opener.open(request, timeout=25) as response:
                content_type = response.headers.get("Content-Type", "")
                declared_length = response.headers.get("Content-Length")
                if declared_length and int(declared_length) > MAX_BYTES:
                    raise ValueError("Playlist is too large (maximum 8 MB).")
                content_bytes = response.read(MAX_BYTES + 1)
            if len(content_bytes) > MAX_BYTES:
                raise ValueError("Playlist is too large (maximum 8 MB).")
            content = content_bytes.decode("utf-8-sig", errors="replace")
            if not content.strip():
                raise ValueError("The provider returned an empty playlist. Check the credentials and subscription.")
            if not content.lstrip().startswith("#EXTM3U"):
                if "text/html" in content_type.lower() or "<html" in content[:1000].lower():
                    raise ValueError("The provider returned a webpage, not an M3U playlist. Verify the server URL and credentials.")
                raise ValueError("The provider response is not a valid M3U playlist.")
            count = sum(1 for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#"))
            body = json.dumps({"content": content, "count": count}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except HTTPError as exc:
            self._send_error(f"Provider returned HTTP {exc.code}. Check the server URL and account status.")
        except (URLError, socket.timeout, TimeoutError) as exc:
            self._send_error("Could not reach the IPTV server: " + str(getattr(exc, "reason", exc))[:180])
        except Exception as exc:
            self._send_error(str(exc)[:300])

    def _send_error(self, message):
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = json.dumps({"ok": True, "service": "Xtream Codes to M3U importer"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
