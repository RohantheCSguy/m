from http.server import BaseHTTPRequestHandler
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlencode
import hashlib
import ipaddress
import json
import socket

MAX_RESPONSE = 4 * 1024 * 1024
MAG_UA = "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 4 rev: 2721 Safari/533.3"


def validate_portal(portal):
    parsed = urlparse(portal)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a valid HTTP or HTTPS portal URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Portal URL must not contain username, password, query or fragment.")
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError("Private or local portal addresses are not allowed.")
    except ValueError as exc:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise exc
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        raise ValueError("Portal hostname could not be resolved.")
    if not infos:
        raise ValueError("Portal hostname could not be resolved.")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("Portal hostname resolves to a private or local address.")
    return parsed


class SameHostRedirect(HTTPRedirectHandler):
    def __init__(self, hostname):
        super().__init__()
        self.hostname = hostname.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = validate_portal(newurl)
        if target.hostname.lower() != self.hostname:
            raise ValueError("Portal redirected to a different host; redirect blocked.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def endpoint_for(portal):
    parsed = urlparse(portal)
    path = parsed.path.rstrip("/")
    if path.endswith("/server/load.php"):
        endpoint_path = path
    else:
        # /c/ is a common MAG portal landing path; API is usually one level above it.
        if path.endswith("/c"):
            path = path[:-2]
        endpoint_path = path + "/server/load.php"
    return parsed.scheme + "://" + parsed.netloc + endpoint_path, parsed


def unwrap_response(data):
    if isinstance(data, dict) and "js" in data:
        js = data["js"]
        if isinstance(js, str):
            try:
                return json.loads(js)
            except Exception:
                return {"raw": js}
        return js
    return data


def portal_call(endpoint, parsed, mac, serial, device, token, params):
    query = dict(params)
    query.setdefault("JsHttpRequest", "1-xml")
    url = endpoint + "?" + urlencode(query)
    device_id = hashlib.md5((serial or mac).encode("utf-8")).hexdigest().upper()
    headers = {
        "User-Agent": MAG_UA,
        "X-User-Agent": "Model: " + device + "; Link: Ethernet",
        "Referer": parsed.scheme + "://" + parsed.netloc + parsed.path.rstrip("/") + "/c/",
        "Accept": "*/*",
        "Connection": "keep-alive",
        "Cookie": "mac=" + mac + "; stb_lang=en; timezone=GMT",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    if serial:
        headers["Serial-Number"] = serial
        headers["Cookie"] += "; sn=" + serial + "; device_id=" + device_id + "; device_id2=" + device_id
    req = Request(url, headers=headers)
    opener = build_opener(SameHostRedirect(parsed.hostname))
    with opener.open(req, timeout=25) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("Portal response exceeds 4 MB. Import a smaller category.")
    try:
        decoded = json.loads(raw.decode("utf-8-sig"))
    except Exception:
        raise ValueError("Portal did not return JSON. Check the portal URL and MAC authorisation.")
    return unwrap_response(decoded)


def list_from_js(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("data", "items", "categories", "results"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/stalker":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 20000:
                raise ValueError("Invalid request size.")
            p = json.loads(self.rfile.read(length))
            portal = str(p.get("portal", "")).strip()
            mac = str(p.get("mac", "")).strip().upper()
            serial = str(p.get("serial", "")).strip()
            device = str(p.get("device", "MAG254")).strip()
            action = p.get("action")
            token = str(p.get("token", "")).strip()
            content_type = p.get("content_type", "itv")
            if not portal or not mac:
                raise ValueError("Portal URL and MAC address are required.")
            if len(portal) > 2048 or len(serial) > 128 or len(mac) > 32:
                raise ValueError("One or more fields are too long.")
            if not __import__("re").fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
                raise ValueError("Invalid MAC address format.")
            if action not in ("handshake", "categories", "items", "link"):
                raise ValueError("Unsupported Stalker action.")
            if content_type not in ("itv", "vod", "series"):
                raise ValueError("Unsupported content type.")
            parsed = validate_portal(portal)
            endpoint, parsed = endpoint_for(portal)
            if action == "handshake":
                result = portal_call(endpoint, parsed, mac, serial, device, "", {
                    "type": "stb", "action": "handshake", "token": ""
                })
                js = result if isinstance(result, dict) else {}
                token_out = js.get("token") or (js.get("data") or {}).get("token")
                if not token_out:
                    raise ValueError("No token returned. Portal may not support this MAC/device profile.")
                output = {"token": str(token_out)}
            elif not token:
                raise ValueError("Authentication token is missing. Connect again.")
            elif action == "categories":
                api_action = "get_genres" if content_type == "itv" else "get_categories"
                result = portal_call(endpoint, parsed, mac, serial, device, token, {
                    "type": content_type, "action": api_action
                })
                output = {"categories": list_from_js(result)}
            elif action == "items":
                category_id = str(p.get("category_id", ""))
                if len(category_id) > 128:
                    raise ValueError("Invalid category ID.")
                params = {
                    "type": content_type,
                    "action": "get_ordered_list",
                    "p": str(max(1, min(int(p.get("page", 1)), 1000))),
                }
                if content_type == "itv":
                    params["genre"] = category_id
                    params["JsHttpRequest"] = "1-xml"
                else:
                    params["category"] = category_id
                result = portal_call(endpoint, parsed, mac, serial, device, token, params)
                output = {"items": list_from_js(result)}
            else:
                cmd = str(p.get("cmd", "")).strip()
                if not cmd or len(cmd) > 4096:
                    raise ValueError("A valid stream command is required.")
                result = portal_call(endpoint, parsed, mac, serial, device, token, {
                    "type": content_type, "action": "create_link", "cmd": cmd,
                    "series": "1" if content_type == "series" else "0"
                })
                js = result if isinstance(result, dict) else {}
                url = js.get("cmd") or js.get("url") or (js.get("data") or {}).get("cmd") or ""
                output = {"url": str(url)}
            body = json.dumps(output, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except HTTPError as exc:
            self._send_error("Portal returned HTTP " + str(exc.code) + ". Verify the portal URL, MAC authorisation and device profile.")
        except (URLError, socket.timeout, TimeoutError) as exc:
            self._send_error("Could not reach portal: " + str(getattr(exc, "reason", exc))[:180])
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
        body = json.dumps({"ok": True, "service": "Stalker Portal MAC importer"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
