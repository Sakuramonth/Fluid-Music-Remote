"""
Fluid Music Remote Pro
A professional Windows media & volume remote controller with fluid web UI.
Author: MikuMikuUi
License: MIT
"""

import asyncio
import base64
import socket
import threading
import io
import time
import logging
from datetime import datetime

# UI & Networking
from flask import Flask, render_template_string, jsonify, request
from waitress import serve
import customtkinter as ctk
from PIL import Image, ImageOps
import pyautogui

# Windows Media & Audio API
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SessionManager
from winrt.windows.storage.streams import DataReader
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL, cast, POINTER, GUID
import comtypes

# Suppress annoying Flask logs
logging.getLogger('werkzeug').disabled = True

class MusicControlApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Fluid Music Remote Pro")
        self.geometry("1200x850")
        ctk.set_appearance_mode("dark")
        
        self.last_title = ""
        self.media_data = {
            "title": "等待同步", "artist": "未连接", 
            "volume": 50, "is_playing": False,
            "pos_sec": 0, "dur_sec": 1
        }
        self.local_pos = 0.0 # High-precision local timer for smooth UI
        
        self.setup_ui()
        
        # Background workers
        threading.Thread(target=self.data_fetch_loop, daemon=True).start()
        threading.Thread(target=self.pc_smooth_tick, daemon=True).start()
        self.after(500, self.start_server)

    def setup_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Left Sidebar: System Logs
        self.sidebar = ctk.CTkFrame(self, width=420, fg_color="#0a0a0a", corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.pack_propagate(False)
        
        ctk.CTkLabel(self.sidebar, text="SYSTEM LOG", font=("Segoe UI", 24, "bold"), text_color="#1DB954").pack(pady=30)
        self.log_view = ctk.CTkTextbox(self.sidebar, fg_color="#000", border_width=1, border_color="#333", font=("Consolas", 15), text_color="#00FF41")
        self.log_view.pack(fill="both", expand=True, padx=20, pady=30)
        self.log_view.configure(state="disabled")

        # Right Main: Immersive Player
        self.main = ctk.CTkFrame(self, fg_color="#050505", corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")

        self.player_card = ctk.CTkFrame(self.main, fg_color="#151515", corner_radius=30, border_width=1, border_color="#222")
        self.player_card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.85, relheight=0.9)

        self.cover_label = ctk.CTkLabel(self.player_card, text="", width=280, height=280)
        self.cover_label.pack(pady=(30, 10))

        self.title_l = ctk.CTkLabel(self.player_card, text="READY", font=("微软雅黑", 34, "bold"), text_color="white")
        self.title_l.pack()
        self.artist_l = ctk.CTkLabel(self.player_card, text="WAITING FOR MEDIA", font=("微软雅黑", 18), text_color="#777")
        self.artist_l.pack(pady=(2, 5))

        # PC Progress
        time_f = ctk.CTkFrame(self.player_card, fg_color="transparent")
        time_f.pack(fill="x", padx=80)
        self.curr_time_l = ctk.CTkLabel(time_f, text="0:00", font=("Consolas", 14), text_color="#999")
        self.curr_time_l.pack(side="left")
        self.total_time_l = ctk.CTkLabel(time_f, text="0:00", font=("Consolas", 14), text_color="#999")
        self.total_time_l.pack(side="right")

        self.pc_progress = ctk.CTkProgressBar(self.player_card, progress_color="#1DB954", height=8, corner_radius=4)
        self.pc_progress.pack(fill="x", padx=80, pady=(0, 20))

        # Media Control Buttons
        btn_f = ctk.CTkFrame(self.player_card, fg_color="transparent")
        btn_f.pack(pady=5)
        ctk.CTkButton(btn_f, text="⏮", width=70, height=70, corner_radius=35, fg_color="#222", font=("Arial", 24), command=lambda: pyautogui.press('prevtrack')).pack(side="left", padx=20)
        self.play_btn = ctk.CTkButton(btn_f, text="▶", width=90, height=90, corner_radius=45, fg_color="#1DB954", text_color="black", font=("Arial", 32), command=lambda: pyautogui.press('playpause'))
        self.play_btn.pack(side="left", padx=20)
        ctk.CTkButton(btn_f, text="⏭", width=70, height=70, corner_radius=35, fg_color="#222", font=("Arial", 24), command=lambda: pyautogui.press('nexttrack')).pack(side="left", padx=20)

        # Volume Bar
        vol_f = ctk.CTkFrame(self.player_card, fg_color="#1a1a1a", height=70, corner_radius=20)
        vol_f.pack(fill="x", padx=60, pady=25)
        self.vol_val_l = ctk.CTkLabel(vol_f, text="50%", font=("Consolas", 20, "bold"), text_color="#1DB954", width=80)
        self.vol_val_l.pack(side="left", padx=20)
        self.vol_slider = ctk.CTkSlider(vol_f, from_=0, to=100, progress_color="#1DB954", button_color="#1DB954", command=self.set_vol)
        self.vol_slider.pack(side="left", fill="x", expand=True, padx=10)

    def fmt_time(self, s):
        m, s = int(s // 60), int(s % 60)
        return f"{m}:{s:02d}"

    def pc_smooth_tick(self):
        while True:
            if self.media_data["is_playing"]:
                self.local_pos = min(self.media_data["dur_sec"], self.local_pos + 0.05)
                p = self.local_pos / self.media_data["dur_sec"] if self.media_data["dur_sec"] > 0 else 0
                self.pc_progress.set(p)
                self.curr_time_l.configure(text=self.fmt_time(self.local_pos))
            time.sleep(0.05)

    def write_log(self, msg, ip="REMOTE"):
        t = datetime.now().strftime("%H:%M:%S")
        self.log_view.configure(state="normal")
        self.log_view.insert("end", f"[{t}] [{ip}] {msg}\n")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def set_vol(self, v):
        try:
            vol_api = get_volume_interface()
            if vol_api: 
                vol_api.SetMasterVolumeLevelScalar(float(v)/100, None)
                self.vol_val_l.configure(text=f"{int(float(v))}%")
                self.media_data["volume"] = int(float(v))
                del vol_api
        except: pass

    def start_server(self):
        ips = [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None) if "." in i[4][0] and not i[4][0].startswith("127.")]
        ip = ips[0] if ips else '127.0.0.1'
        self.write_log(f"SERVER STARTED ON http://{ip}:5000", "SYSTEM")
        threading.Thread(target=self.run_flask, daemon=True).start()

    def data_fetch_loop(self):
        while True:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                info = loop.run_until_complete(get_all_media_info())
                loop.close()
                if info:
                    if abs(self.local_pos - info['pos_sec']) > 2.0:
                        self.local_pos = info['pos_sec']
                    self.media_data.update(info)
                    if not info['is_playing']: self.local_pos = info['pos_sec']

                    self.vol_slider.set(info['volume'])
                    self.vol_val_l.configure(text=f"{info['volume']}%")
                    self.play_btn.configure(text="⏸" if info['is_playing'] else "▶")
                    self.total_time_l.configure(text=self.fmt_time(info['dur_sec']))
                    
                    if info['title'] != self.last_title:
                        self.title_l.configure(text=info['title'][:18])
                        self.artist_l.configure(text=info['artist'][:25])
                        if info.get('thumbnail_raw'):
                            pil_img = Image.open(io.BytesIO(info['thumbnail_raw']))
                            pil_img = ImageOps.fit(pil_img, (280, 280), Image.Resampling.LANCZOS)
                            ctk_img = ctk.CTkImage(pil_img, size=(280, 280))
                            self.cover_label.configure(image=ctk_img)
                        self.last_title = info['title']
            except: pass
            time.sleep(1.0)

    def run_flask(self):
        app = Flask(__name__)
        @app.route('/')
        def index(): return render_template_string(HTML_WEB)
        @app.route('/info')
        def info():
            d = self.media_data.copy()
            d['pos_sec'] = self.local_pos
            if 'thumbnail_raw' in d: del d['thumbnail_raw']
            return jsonify(d)
        @app.route('/control/<action>')
        def control(action):
            m = {'playpause':'playpause', 'prev':'prevtrack', 'next':'nexttrack'}
            if action in m: 
                pyautogui.press(m[action])
                self.write_log(f"ACTION: {action.upper()}", request.remote_addr)
            return "OK"
        @app.route('/set_volume')
        def set_volume():
            v = request.args.get('v', 50)
            self.set_vol(float(v))
            return "OK"
        serve(app, host='0.0.0.0', port=5000, threads=12)

# Mobile Web Template
HTML_WEB = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Fluid Remote</title>
    <style>
        :root { --primary: #1DB954; }
        body { margin: 0; background: #000; color: white; font-family: -apple-system, system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; overflow: hidden; }
        #fluid-bg { position: fixed; inset: 0; z-index: -1; background: linear-gradient(45deg, #0f0f0f, #1a1a1a, #0a0a0a, #1d1d1d); background-size: 400% 400%; animation: fluidMove 15s ease infinite; }
        @keyframes fluidMove { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
        #cover-bg { position: fixed; inset: 0; background-size: cover; background-position: center; filter: blur(80px) brightness(0.2); z-index: -1; transition: 1.5s ease; }
        .album-art { width: 250px; height: 250px; border-radius: 25px; overflow: hidden; box-shadow: 0 15px 40px rgba(0,0,0,0.8); margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1); }
        .album-art img { width: 100%; height: 100%; object-fit: cover; }
        .info { text-align: center; width: 85%; margin-bottom: 20px; }
        .info h1 { font-size: 24px; margin: 0; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .progress-area { width: 85%; margin-bottom: 25px; }
        .prog-track { width: 100%; height: 5px; background: rgba(255,255,255,0.1); border-radius: 3px; position: relative; }
        #prog-bar { width: 0%; height: 100%; background: var(--primary); border-radius: 3px; }
        .time-labels { display: flex; justify-content: space-between; font-size: 11px; color: #aaa; margin-top: 8px; font-family: monospace; }
        .controls { display: flex; align-items: center; gap: 40px; margin-bottom: 30px; }
        .btn-svg { fill: white; width: 32px; height: 32px; }
        .play-main { width: 75px; height: 75px; background: white; border-radius: 50%; display: flex; align-items: center; justify-content: center; }
        .play-main svg { fill: black; width: 35px; height: 35px; }
        .vol-card { width: 85%; background: rgba(255,255,255,0.07); padding: 18px; border-radius: 22px; backdrop-filter: blur(10px); }
        .vol-header { display: flex; justify-content: space-between; font-weight: 700; font-size: 14px; margin-bottom: 12px; color: var(--primary); }
        .vol-slider-box { display: flex; align-items: center; gap: 15px; }
        .v-btn { font-size: 22px; width: 44px; height: 44px; background: rgba(255,255,255,0.1); border-radius: 50%; display: flex; align-items: center; justify-content: center; user-select: none; }
        input[type=range] { flex: 1; height: 5px; -webkit-appearance: none; background: #333; border-radius: 10px; outline: none; }
        input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 22px; height: 22px; background: white; border-radius: 50%; border: 3px solid var(--primary); }
    </style>
</head>
<body>
    <div id="fluid-bg"></div><div id="cover-bg"></div>
    <div class="album-art"><img id="cover" src=""></div>
    <div class="info"><h1 id="title">正在连接...</h1><p id="artist">获取媒体中</p></div>
    <div class="progress-area">
        <div class="prog-track"><div id="prog-bar"></div></div>
        <div class="time-labels"><span id="curr-t">0:00</span><span id="total-t">0:00</span></div>
    </div>
    <div class="controls">
        <svg class="btn-svg" onclick="api('prev')" viewBox="0 0 24 24"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
        <div class="play-main" onclick="api('playpause')"><svg id="p-icon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>
        <svg class="btn-svg" onclick="api('next')" viewBox="0 0 24 24"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
    </div>
    <div class="vol-card">
        <div class="vol-header"><span>VOLUME</span><span id="v-val">50%</span></div>
        <div class="vol-slider-box">
            <div class="v-btn" onclick="v_step(-5)">−</div>
            <input type="range" id="v-slider" min="0" max="100" oninput="v_sync_realtime(this.value)">
            <div class="v-btn" onclick="v_step(5)">+</div>
        </div>
    </div>
    <script>
        let isSliding = false, slideEndTimer = null, lastSendTime = 0;
        let curPos = 0, duration = 1, isPlaying = false;
        function fmtTime(s) { let m = Math.floor(s/60); s = Math.floor(s%60); return m + ":" + (s<10?"0":"") + s; }
        
        setInterval(() => { if (isPlaying && !isSliding) { curPos = Math.min(duration, curPos + 0.1); document.getElementById('prog-bar').style.width = (curPos/duration*100) + "%"; document.getElementById('curr-t').innerText = fmtTime(curPos); } }, 100);
        
        function v_sync_realtime(v) { 
            isSliding = true; document.getElementById('v-val').innerText = v + '%'; 
            const now = Date.now(); 
            if (now - lastSendTime > 60) { fetch('/set_volume?v=' + v); lastSendTime = now; } 
            clearTimeout(slideEndTimer); slideEndTimer = setTimeout(() => { isSliding = false; }, 1200); 
        }

        function v_step(s) {
            let i = document.getElementById('v-slider'); 
            let newVal = Math.min(100, Math.max(0, parseInt(i.value) + s));
            i.value = newVal; v_sync_realtime(newVal);
        }

        async function sync() { 
            if (isSliding) return; 
            try { 
                const r = await fetch('/info?t=' + Date.now()); 
                const d = await r.json(); 
                if(d.title) document.getElementById('title').innerText = d.title;
                if(d.artist) document.getElementById('artist').innerText = d.artist;
                document.getElementById('v-val').innerText = d.volume + '%'; 
                document.getElementById('v-slider').value = d.volume; 
                curPos = d.pos_sec; duration = d.dur_sec; isPlaying = d.is_playing; 
                document.getElementById('total-t').innerText = fmtTime(duration); 
                document.getElementById('prog-bar').style.width = (curPos/duration*100) + "%"; 
                if(d.thumbnail) { document.getElementById('cover').src = d.thumbnail; document.getElementById('cover-bg').style.backgroundImage = `url(${d.thumbnail})`; } 
                document.getElementById('p-icon').innerHTML = d.is_playing ? '<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>' : '<path d="M8 5v14l11-7z"/>'; 
            } catch(e) {} 
        }
        function api(x) { fetch('/control/'+x); setTimeout(sync, 250); }
        window.onload = sync;
        setInterval(sync, 1500); 
    </script>
</body>
</html>
"""

async def get_all_media_info():
    try:
        manager = await SessionManager.request_async()
        session = manager.get_current_session()
        if session:
            props = await session.try_get_media_properties_async()
            playback = session.get_playback_info()
            timeline = session.get_timeline_properties()
            img_b64 = ""; raw_img = None
            if props.thumbnail:
                stream = await props.thumbnail.open_read_async()
                reader = DataReader(stream.get_input_stream_at(0))
                await reader.load_async(stream.size)
                buffer = bytearray(stream.size)
                reader.read_bytes(buffer)
                raw_img = buffer
                img_b64 = f"data:image/png;base64,{base64.b64encode(buffer).decode('utf-8')}"
            vol_api = get_volume_interface()
            vol = int(vol_api.GetMasterVolumeLevelScalar() * 100) if vol_api else 50
            if vol_api: del vol_api
            return {
                "title": props.title or "Unknown", "artist": props.artist or "Player",
                "thumbnail": img_b64, "thumbnail_raw": raw_img, "is_playing": playback.playback_status == 4, 
                "volume": vol, "pos_sec": timeline.position.total_seconds(), "dur_sec": max(1, timeline.end_time.total_seconds())
            }
    except: pass
    return None

def get_volume_interface():
    try:
        devices = AudioUtilities.GetDeviceEnumerator()
        interface = devices.GetDefaultAudioEndpoint(0, 1).Activate(GUID(IAudioEndpointVolume._iid_), CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except: return None

if __name__ == "__main__":

    MusicControlApp().mainloop()
