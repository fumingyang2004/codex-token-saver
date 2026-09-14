"""Single-instance, token-protected localhost dashboard. No telemetry uploads."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen
import webbrowser

from . import control, sessions
from .state import read_json, write_json

WEB = Path(__file__).parent/"web"


def request(info,path="/api/status",method="GET"):
    req=Request(f"http://127.0.0.1:{info['port']}{path}",headers={"X-Saver-Token":info["token"]},method=method)
    return json.load(urlopen(req,timeout=2))


def running(store):
    try:
        info=read_json(store.root/"ui.json")
        response=request(info)
        return info if response.get("instance") == info.get("instance") else None
    except Exception: return None


def start(store,open_browser=True):
    with store.lock():
        info=running(store)
        if not info:
            kwargs={"stdin":subprocess.DEVNULL,"stdout":subprocess.DEVNULL,"stderr":subprocess.DEVNULL}
            if os.name == "nt": kwargs["creationflags"]=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else: kwargs["start_new_session"]=True
            subprocess.Popen([*control.prefix(store),"_ui-serve"],**kwargs)
            for _ in range(100):
                time.sleep(.1)
                info=running(store)
                if info: break
            if not info: raise RuntimeError("Local dashboard did not start")
    url=f"http://127.0.0.1:{info['port']}/?token={info['token']}"
    if open_browser: webbrowser.open(url)
    return url


def stop(store):
    info=running(store)
    if info: request(info,"/api/stop","POST")
    return 0


def serve(store):
    from .cli import status
    token=secrets.token_urlsafe(24)
    instance=secrets.token_hex(12)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,data,status_code=200,kind="application/json"):
            payload=json.dumps(data).encode() if kind == "application/json" else data
            self.send_response(status_code)
            self.send_header("Content-Type",kind+"; charset=utf-8")
            self.send_header("Content-Length",str(len(payload)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(payload)
        def allowed(self):
            own=f"127.0.0.1:{self.server.server_port}"
            return self.headers.get("Host") == own and self.headers.get("Origin",f"http://{own}") == f"http://{own}"
        def do_GET(self):
            if not self.allowed(): return self.send({"error":"forbidden"},403)
            parts=urlsplit(self.path)
            if parts.path.startswith("/api/"):
                if not secrets.compare_digest(self.headers.get("X-Saver-Token",""),token): return self.send({"error":"forbidden"},403)
                if parts.path == "/api/status": return self.send({**status(store),"instance":instance})
                if parts.path in ("/api/session/current","/api/session/current/events"):
                    sid=parse_qs(parts.query).get("session",[None])[0]
                    try: value=sessions.snapshot(store,sid)
                    except Exception: return self.send({"error":"Session data temporarily unavailable"},503)
                    return self.send(value if not parts.path.endswith("/events") else (value["summary"] or {}).get("events",[]))
                return self.send({"error":"not found"},404)
            assets={"/":("index.html","text/html"),"/app.js":("app.js","application/javascript"),"/style.css":("style.css","text/css")}
            if parts.path not in assets: return self.send({"error":"not found"},404)
            filename,kind=assets[parts.path]
            return self.send((WEB/filename).read_bytes(),kind=kind)
        def do_POST(self):
            if not self.allowed() or not secrets.compare_digest(self.headers.get("X-Saver-Token",""),token): return self.send({"error":"forbidden"},403)
            if self.path == "/api/stop":
                self.send({"stopped":True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
            else: self.send({"error":"not found"},404)
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    write_json(store.root/"ui.json",{"port":server.server_port,"token":token,"instance":instance,"pid":os.getpid()})
    try: server.serve_forever(poll_interval=.25)
    finally:
        server.server_close()
        current=read_json(store.root/"ui.json")
        if current.get("instance") == instance: (store.root/"ui.json").unlink(missing_ok=True)
    return 0
