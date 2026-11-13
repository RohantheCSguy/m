from http.server import BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
import ipaddress
import json
import socket


def validate_public_url(url):
    """Allow only public HTTP(S) destinations; block localhost/private networks."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only valid HTTP/HTTPS URLs are supported")

    host = parsed.hostname

    # Reject literal private, loopback, link-local, multicast and reserved IPs.
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError("Non-public IP addresses are not allowed")
        return
    except ValueError as exc:
        # If it was an IP literal that failed the global check, keep the rejection.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise exc

    # Resolve DNS and reject hostnames that point to non-public addresses.
    addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    if not addresses:
        raise ValueError("Hostname did not resolve")

    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global:
            raise ValueError("Hostname resolves to a non-public IP address")


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_url(url):
    try:
        validate_public_url(url)

        opener = build_opener(SafeRedirectHandler)
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; M3U-Manager/1.0)",
                "Range": "bytes=0-4095",
                "Accept": "*/*",
            },
        )

        with opener.open(request, timeout=7) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            data = response.read(4096)

            is_hls = (
                ".m3u8" in url.lower()
                or "mpegurl" in content_type.lower()
            )

            if is_hls and b"#EXTM3U" not in data:
                return {
                    "status": "uncertain",
                    "detail": f"HTTP {status}, but HLS header was not found",
                }

            if not data:
                return {
                    "status": "uncertain",
                    "detail": f"HTTP {status}, empty response",
                }

            return {
                "status": "working",
                "detail": f"HTTP {status} · {content_type or 'data received'}",
            }

    except HTTPError as exc:
        return {"status": "failed", "detail": f"HTTP {exc.code} {exc.reason}"}
    except (URLError, socket.timeout, TimeoutError, ValueError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"status": "failed", "detail": str(reason)[:150]}
    except Exception as exc:
        return {"status": "uncertain", "detail": str(exc)[:150]}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/check":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 200_000:
                raise ValueError("Invalid request size")

            payload = json.loads(self.rfile.read(length))
            urls = payload.get("urls", [])

            if not isinstance(urls, list) or len(urls) > 60:
                raise ValueError("Send a maximum of 60 URLs per request")

            clean_urls = list(dict.fromkeys(
                u for u in urls if isinstance(u, str)
            ))

            results = {}
            with ThreadPoolExecutor(max_workers=12) as executor:
                futures = {
                    executor.submit(check_url, url): url
                    for url in clean_urls
                }
                for future in as_completed(futures):
                    url = futures[future]
                    results[url] = future.result()

            body = json.dumps(results).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self):
        body = json.dumps({
            "ok": True,
            "service": "M3U Manager link checker",
            "usage": "POST /api/check with JSON {\"urls\": [\"https://...\"]}"
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
