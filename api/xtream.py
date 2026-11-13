from http.server import BaseHTTPRequestHandler
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlencode
import ipaddress
import json
import socket

# Keep each response small enough for serverless function response limits.
MAX_JSON_BYTES = 4 * 1024 * 1024


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
            raise ValueError("The provider redirected to a different host; redirect blocked.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(base, parsed, username, password, action, category_id=None):
    params = {"username": username, "password": password, "action": action}
    if category_id is not None:
        params["category_id"] = str(category_id)
    url = base + "/player_api.php?" + urlencode(params)
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; M3U-Manager/1.1)",
        "Accept": "application/json, text/plain, */*",
    })
    opener = build_opener(SameHostRedirectHandler(parsed.hostname))
    with opener.open(request, timeout=25) as response:
        raw = response.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("This category's response exceeds 4 MB. Try importing smaller categories separately.")
    try:
        result = json.loads(raw.decode("utf-8-sig"))
    except Exception:
        raise ValueError("The provider did not return valid Xtream API JSON. Verify the host and account.")
    if isinstance(result, dict) and result.get("user_info", {}).get("auth") == 0:
        raise ValueError("Xtream login rejected. Check username, password and subscription.")
    if isinstance(result, dict) and result.get("error"):
        raise ValueError(str(result["error"])[:200])
    if not isinstance(result, list):
        raise ValueError("Unexpected Xtream API response for " + action + ".")
    return result


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
            action = payload.get("action", "")
            category_id = payload.get("category_id")
            if not all(isinstance(v, str) and v.strip() for v in (host, username, password)):
                raise ValueError("Server URL, username and password are required.")
            if len(host) > 2048 or len(username) > 512 or len(password) > 1024:
                raise ValueError("One or more fields are too long.")
            if action not in ("categories", "streams"):
                raise ValueError("Unsupported Xtream action.")
            parsed = validate_public_url(host.strip().rstrip("/"))
            base = f"{parsed.scheme}://{parsed.netloc}"
            if action == "categories":
                result = fetch_json(base, parsed, username, password, "get_live_categories")
                body_obj = {"categories": result}
            else:
                if category_id is None or len(str(category_id)) > 128:
                    raise ValueError("A valid category ID is required.")
                result = fetch_json(base, parsed, username, password, "get_live_streams", category_id)
                body_obj = {"streams": result}
            body = json.dumps(body_obj, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except HTTPError as exc:
            self._send_error(f"Provider returned HTTP {exc.code}. Check the server URL and account.")
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
        body = json.dumps({"ok": True, "service": "Xtream category importer"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
