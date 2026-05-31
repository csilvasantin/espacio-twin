#!/usr/bin/env python3
"""Mini-servidor local del gemelo del espacio.

Sirve la app y hace de puente para importar fotos desde el movil por LAN:
  GET  /info      -> { lan_ip, port }
  GET  /latest?since=N -> 200 con la imagen si hay una mas nueva que N, si no 204
  POST /upload    -> recibe los bytes de la imagen (Content-Type = tipo de la foto)
  (resto)         -> ficheros estaticos (index.html, phone.html, ...)

Uso:  python3 serve.py [puerto]   (por defecto 8090, escucha en 0.0.0.0)
"""
import http.server
import socketserver
import socket
import sys
import os
import threading
import json
from urllib.parse import urlparse, parse_qs

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
# URL publica (p.ej. Funnel de Tailscale) que el movil usara para llegar aqui.
# Se pasa como argv[2] o variable de entorno TWIN_PUBLIC_URL. Sin barra final.
PUBLIC_URL = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TWIN_PUBLIC_URL", "")).rstrip("/")
DIR = os.path.dirname(os.path.abspath(__file__))
# Carpeta del diario (de donde salen las fotos reales de las gafas). El sync las
# deja en media/glasses/<fecha>/<id>.jpg e indexa en manifest.json.
DIARY_DIR = os.environ.get("DIARY_DIR", os.path.expanduser("~/Projects/csilvasantin/18.-diario"))
GMEDIA = os.path.realpath(os.path.join(DIARY_DIR, "media", "glasses"))

state = {"id": 0, "bytes": None, "ctype": "image/jpeg"}
lock = threading.Lock()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DIR, **k)

    def log_message(self, *a):
        pass  # silencioso

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/info":
            return self._json({"lan_ip": lan_ip(), "port": PORT, "public_url": PUBLIC_URL})
        if u.path == "/glasses-manifest":
            # Manifest local de las fotos de las gafas (mismo origen -> sin CORS).
            mpath = os.path.join(GMEDIA, "manifest.json")
            try:
                with open(mpath, "rb") as f:
                    data = f.read()
            except OSError:
                return self._json({"error": "sin manifest de gafas", "path": mpath}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/glasses-photo":
            # Sirve una foto de las gafas por su ruta relativa del manifest
            # (media/glasses/<fecha>/<id>.jpg). Se valida que no escape de GMEDIA.
            rel = parse_qs(u.query).get("file", [""])[0]
            target = os.path.realpath(os.path.join(DIARY_DIR, rel))
            if not rel or os.path.commonpath([target, GMEDIA]) != GMEDIA or not os.path.isfile(target):
                return self._json({"error": "foto no encontrada"}, 404)
            ext = os.path.splitext(target)[1].lower()
            ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                     "mp4": "video/mp4"}.get(ext.lstrip("."), "application/octet-stream")
            try:
                with open(target, "rb") as f:
                    data = f.read()
            except OSError:
                return self._json({"error": "no se pudo leer la foto"}, 500)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "max-age=3600")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/latest":
            since = int(parse_qs(u.query).get("since", ["0"])[0])
            with lock:
                cur, data, ctype = state["id"], state["bytes"], state["ctype"]
            if cur > since and data is not None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("X-Image-Id", str(cur))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(204)
                self.send_header("X-Image-Id", str(cur))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.end_headers()
            return
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/upload":
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length) if length else b""
            ctype = self.headers.get("Content-Type", "image/jpeg")
            with lock:
                state["id"] += 1
                state["bytes"] = data
                state["ctype"] = ctype
                newid = state["id"]
            return self._json({"ok": True, "id": newid, "bytes": len(data)})
        self.send_response(404)
        self.end_headers()


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    ip = lan_ip()
    print(f"espacio-twin  ->  http://{ip}:{PORT}/")
    print(f"movil (misma WiFi)  ->  http://{ip}:{PORT}/phone.html")
    ThreadedServer(("0.0.0.0", PORT), Handler).serve_forever()
