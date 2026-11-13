from http.server import BaseHTTPRequestHandler
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
import ipaddress
import json
import socket

MAX_BYTES = 8 * 1024 * 1024


def validate_public_url(url):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a valid HTTP or HTTPS playlist URL.")

    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded usernames or passwords are not supported.")

    host = parsed.hostname

    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError("Private or local network addresses are not allowed.")
        return
    except ValueError as exc:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise exc

    try:
        addresses = socket.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except socket.gaierror:
        raise ValueError("The playlist hostname could not be resolved.")

    if not addresses:
        raise ValueError("The playlist hostname could not be resolved.")

    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global:
            raise ValueError("The playlist hostname resolves to a private or local address.")


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/import":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError("Invalid request size.")

            payload = json.loads(self.rfile.read(length))
            url = payload.get("url", "")

            if not isinstance(url, str) or len(url) > 4096:
                raise ValueError("Please provide a valid playlist URL.")

            url = url.strip()
            validate_public_url(url)

            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; M3U-Manager/1.0)",
                    "Accept": "audio/x-mpegurl, application/vnd.apple.mpegurl, text/plain, */*",
                },
            )

            opener = build_opener(SafeRedirectHandler)

            with opener.open(request, timeout=18) as response:
                content_type = response.headers.get("Content-Type", "")
                declared_length = response.headers.get("Content-Length")

                if declared_length:
                    try:
                        if int(declared_length) > MAX_BYTES:
                            raise ValueError("Playlist is too large (maximum 8 MB).")
                    except ValueError as exc:
                        if "too large" in str(exc):
                            raise
                content_bytes = response.read(MAX_BYTES + 1)

            if len(content_bytes) > MAX_BYTES:
                raise ValueError("Playlist is too large (maximum 8 MB).")

            # M3U playlists are commonly UTF-8; tolerate a UTF-8 BOM.
            content = content_bytes.decode("utf-8-sig", errors="replace")

            if not content.strip():
                raise ValueError("The URL returned an empty file.")

            if not content.lstrip().startswith("#EXTM3U"):
                if "text/html" in content_type.lower() or "<html" in content[:1000].lower():
                    raise ValueError("The URL returned a webpage, not an M3U playlist. Use the direct .m3u/.m3u8 URL.")
                raise ValueError("The response is not a valid M3U playlist (missing #EXTM3U header).")

            name = urlparse(url).path.rsplit("/", 1)[-1] or "Remote playlist"
            if not name.lower().endswith((".m3u", ".m3u8")):
                name = "Remote playlist"

            body = json.dumps({
                "content": content,
                "name": name,
                "content_type": content_type,
            }).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        except HTTPError as exc:
            self._send_error(f"Playlist server returned HTTP {exc.code}.")
        except (URLError, socket.timeout, TimeoutError) as exc:
            self._send_error("Could not reach the playlist URL: " + str(getattr(exc, "reason", exc))[:180])
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
        body = json.dumps({
            "ok": True,
            "service": "M3U Manager playlist importer",
            "usage": "POST /api/import with JSON {\"url\":\"https://.../playlist.m3u\"}"
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
