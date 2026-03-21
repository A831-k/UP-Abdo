#!/usr/bin/env python3
"""
FileShare Server
Usage: python server.py
"""

import os
import uuid
import mimetypes
import json
import socket
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
import base64
from urllib.parse import urlparse

# Icon data (embedded)
ICON_DATA = base64.b64decode("")

HOST = "0.0.0.0"
PORT = 8081
UPLOAD_DIR = "uploads"
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
DB_FILE = "files_db.json"

os.makedirs(UPLOAD_DIR, exist_ok=True)

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_db():
    with open(DB_FILE, "w") as f:
        json.dump(files_db, f, ensure_ascii=False)

files_db = load_db()


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def parse_multipart(data: bytes, boundary: str):
    boundary_bytes = boundary.encode()
    parts = data.split(b"--" + boundary_bytes)
    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        if b"\r\n\r\n" in part:
            headers_raw, body = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            headers_raw, body = part.split(b"\n\n", 1)
        else:
            continue
        headers = headers_raw.decode("utf-8", errors="replace")
        if 'name="file"' not in headers:
            continue
        m = re.search(r'filename="([^"]+)"', headers)
        if not m:
            continue
        filename = m.group(1)
        if body.endswith(b"\r\n"):
            body = body[:-2]
        elif body.endswith(b"\n"):
            body = body[:-1]
        return filename, body
    return None, None


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[REQ] {self.address_string()} - {fmt % args}")

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.serve_static()
        elif path == "/manifest.json":
            self.serve_file("manifest.json", "application/json")
        elif path == "/sw.js":
            self.serve_file("sw.js", "application/javascript")
        elif path == "/icon.png":
            self.serve_icon()
        elif path.startswith("/download/"):
            self.do_download(path.split("/download/", 1)[1].strip("/"))
        elif path.startswith("/delete/"):
            parts = path.split("/")
            if len(parts) == 4:
                self.do_delete(parts[2], parts[3])
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if urlparse(self.path).path == "/upload":
            self.do_upload()
        else:
            self.send_response(404)
            self.end_headers()

    def serve_static(self):
        base = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(base, "index.html")
        if not os.path.exists(html_path):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"index.html not found")
            return
        with open(html_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.cors()
        self.end_headers()
        self.wfile.write(data)

    def serve_icon(self):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", len(ICON_DATA))
        self.cors()
        self.end_headers()
        self.wfile.write(ICON_DATA)

    def serve_file(self, filename, mime):
        base = os.path.dirname(os.path.abspath(__file__))
        fp = os.path.join(base, filename)
        if not os.path.exists(fp):
            self.send_response(404)
            self.end_headers()
            return
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(data))
        self.cors()
        self.end_headers()
        self.wfile.write(data)

    def base_url(self):
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        proto = self.headers.get("X-Forwarded-Proto", "")
        if not proto:
            if "trycloudflare.com" in host or "serveo" in host:
                proto = "https"
            else:
                proto = "http"
        if host:
            if "localhost" in host or host.split(":")[0].replace(".", "").isdigit():
                return f"http://{host.split(':')[0]}:{PORT}"
            return f"{proto}://{host}"
        return f"http://{get_local_ip()}:{PORT}"

    def do_upload(self):
        try:
            ct = self.headers.get("Content-Type", "")
            cl = int(self.headers.get("Content-Length", 0))

            if cl > MAX_FILE_SIZE:
                return self.json({"error": "File too large (max 20MB)"}, 413)
            if "multipart/form-data" not in ct:
                return self.json({"error": "Invalid content type"}, 400)

            boundary = ""
            for p in ct.split(";"):
                p = p.strip()
                if p.startswith("boundary="):
                    boundary = p[9:].strip()
                    break

            if not boundary:
                return self.json({"error": "No boundary found"}, 400)

            raw = self.rfile.read(cl)
            filename, filebytes = parse_multipart(raw, boundary)

            if not filename or filebytes is None:
                return self.json({"error": "No valid file received"}, 400)

            token = uuid.uuid4().hex[:16]
            ext = os.path.splitext(filename)[1]
            saved = f"{token}{ext}"
            filepath = os.path.join(UPLOAD_DIR, saved)

            with open(filepath, "wb") as f:
                f.write(filebytes)

            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            delete_token = uuid.uuid4().hex[:32]
            files_db[token] = {
                "original_name": filename,
                "saved_name": saved,
                "size": len(filebytes),
                "mime": mime,
                "delete_token": delete_token
            }

            base = self.base_url()
            url = f"{base}/download/{token}"
            delete_url = f"{base}/delete/{token}/{delete_token}"
            print(f"[UP] {filename} ({len(filebytes):,} bytes) -> {url}")
            save_db()

            self.json({
                "success": True,
                "token": token,
                "original_name": filename,
                "size": len(filebytes),
                "download_url": url,
                "delete_url": delete_url
            })

        except Exception as e:
            print(f"[ERR] {e}")
            self.json({"error": str(e)}, 500)

    def do_download(self, token):
        if token not in files_db:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"File not found")
            return
        info = files_db[token]
        fp = os.path.join(UPLOAD_DIR, info["saved_name"])
        if not os.path.exists(fp):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"File deleted")
            return
        with open(fp, "rb") as f:
            data = f.read()
        try:
            safe = info["original_name"].encode("utf-8").decode("latin-1")
        except:
            safe = token + os.path.splitext(info["saved_name"])[1]
        self.send_response(200)
        self.send_header("Content-Type", info["mime"])
        self.send_header("Content-Length", len(data))
        self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.cors()
        self.end_headers()
        self.wfile.write(data)
        print(f"[DL] {info['original_name']}")

    def do_delete(self, token, delete_token):
        if token not in files_db:
            return self.json({"error": "File not found"}, 404)
        info = files_db[token]
        if info.get("delete_token") != delete_token:
            return self.json({"error": "Invalid delete token"}, 403)
        fp = os.path.join(UPLOAD_DIR, info["saved_name"])
        if os.path.exists(fp):
            os.remove(fp)
        del files_db[token]
        save_db()
        print(f"[DEL] {info['original_name']} deleted")
        self.json({"success": True, "message": "File deleted"})

    def do_info(self, token):
        if token not in files_db:
            return self.json({"error": "Not found"}, 404)
        self.json(files_db[token])

    def json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.cors()
        self.end_headers()
        self.wfile.write(body)


def main():
    ip = get_local_ip()
    print("=" * 50)
    print("  FileShare Server - Ready")
    print("=" * 50)
    print(f"  Local : http://{ip}:{PORT}")
    print(f"  Folder: {os.path.abspath(UPLOAD_DIR)}")
    print("-" * 50)
    print(f"  Cloudflare tunnel (run in another window):")
    print(f"  ~/cloudflared tunnel --url http://127.0.0.1:{PORT}")

    print("=" * 50)
    print("  Ctrl+C to stop\n")
    server = HTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[STOP] Server stopped")
        server.server_close()


if __name__ == "__main__":
    main()
