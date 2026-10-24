"""
YouTube Private & Members-Only Video Player/Downloader
=======================================================
A GUI tool powered by yt-dlp that lets you:
  - Play/Stream YouTube private, unlisted, and members-only videos
  - Download them in any quality
  - Uses YOUR OWN browser cookies for authentication (you must already have access)

Requirements:
  pip install -r requirements.txt
  + ffmpeg on PATH (for merging streams)
  + mpv or VLC (optional, for streaming playback)

Usage:
  python youtube_private_player.py
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox, StringVar, BooleanVar, IntVar, END

try:
    import customtkinter as ctk
except ImportError:
    print("customtkinter not found. Installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "customtkinter"])
    import customtkinter as ctk

try:
    import yt_dlp
except ImportError:
    print("yt-dlp not found. Installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp"])
    import yt_dlp


# ───────────────────────────── Constants ─────────────────────────────

APP_NAME = "YT Private Player"
APP_VERSION = "1.0.0"
DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads" / "YT_Private_Player")
CONFIG_FILE = str(Path.home() / ".yt_private_player_config.json")

BROWSERS = ["chrome", "firefox", "edge", "brave", "opera", "vivaldi", "chromium"]

QUALITY_OPTIONS = {
    "Best (Auto)": "bv*+ba/b",
    "1080p": "bv[height<=1080]+ba/b[height<=1080]",
    "720p": "bv[height<=720]+ba/b[height<=720]",
    "480p": "bv[height<=480]+ba/b[height<=480]",
    "360p": "bv[height<=360]+ba/b[height<=360]",
    "Audio Only (Best)": "ba/b",
    "Audio Only (MP3)": "ba/b",
}

PLAYERS = ["(Built-in yt-dlp)", "mpv", "vlc"]


# ───────────────────────────── Config Persistence ─────────────────────

def load_config() -> dict:
    """Load saved configuration from disk."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg: dict):
    """Persist configuration to disk."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ───────────────────────────── yt-dlp Core ───────────────────────────

class YTEngine:
    """Thin wrapper around yt-dlp Python API."""

    @staticmethod
    def _build_opts(
        *,
        cookies_from_browser: str | None = None,
        cookies_file: str | None = None,
        quality: str = "bv*+ba/b",
        output_dir: str = DEFAULT_DOWNLOAD_DIR,
        output_template: str = "%(title)s [%(id)s].%(ext)s",
        merge_format: str = "mp4",
        progress_hook=None,
        postprocessors: list | None = None,
        simulate: bool = False,
    ) -> dict:
        opts: dict = {
            "format": quality,
            "merge_output_format": merge_format,
            "outtmpl": os.path.join(output_dir, output_template),
            "noplaylist": False,
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
        }
        if cookies_from_browser:
            opts["cookiesfrombrowser"] = (cookies_from_browser,)
        if cookies_file:
            opts["cookiefile"] = cookies_file
        if progress_hook:
            opts["progress_hooks"] = [progress_hook]
        if postprocessors:
            opts["postprocessors"] = postprocessors
        if simulate:
            opts["simulate"] = True
            opts["skip_download"] = True
        return opts

    @staticmethod
    def fetch_info(
        url: str,
        cookies_from_browser: str | None = None,
        cookies_file: str | None = None,
    ) -> dict | None:
        """Fetch video metadata without downloading."""
        opts = YTEngine._build_opts(
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
            simulate=True,
        )
        opts["extract_flat"] = False
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def download(
        url: str,
        *,
        cookies_from_browser: str | None = None,
        cookies_file: str | None = None,
        quality: str = "bv*+ba/b",
        output_dir: str = DEFAULT_DOWNLOAD_DIR,
        audio_only: bool = False,
        progress_hook=None,
    ) -> dict:
        """Download video/audio to disk."""
        postprocessors = []
        if audio_only:
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            })

        opts = YTEngine._build_opts(
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
            quality=quality,
            output_dir=output_dir,
            postprocessors=postprocessors if postprocessors else None,
            progress_hook=progress_hook,
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return {"status": "ok", "info": info}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @staticmethod
    def get_stream_url(
        url: str,
        cookies_from_browser: str | None = None,
        cookies_file: str | None = None,
        quality: str = "bv*+ba/b",
    ) -> str | None:
        """Resolve the direct stream URL for piping into a player."""
        opts = YTEngine._build_opts(
            cookies_from_browser=cookies_from_browser,
            cookies_file=cookies_file,
            quality=quality,
            simulate=True,
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get("url") or info.get("requested_formats", [{}])[0].get("url")
        except Exception:
            return None

    @staticmethod
    def stream_in_player(
        url: str,
        player: str = "mpv",
        cookies_from_browser: str | None = None,
        cookies_file: str | None = None,
    ):
        """Open the video directly in an external media player."""
        cmd = ["yt-dlp"]
        if cookies_from_browser:
            cmd.extend(["--cookies-from-browser", cookies_from_browser])
        elif cookies_file:
            cmd.extend(["--cookies", cookies_file])

        # yt-dlp supports --player for mpv / vlc direct handoff
        if player.lower() == "mpv":
            cmd.extend(["--player", "mpv"])
        elif player.lower() == "vlc":
            cmd.extend(["--player", "vlc"])

        cmd.append(url)
        try:
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            return {"status": "ok"}
        except FileNotFoundError:
            return {"status": "error", "error": f"'{player}' or 'yt-dlp' not found on PATH."}
        except Exception as e:
            return {"status": "error", "error": str(e)}


# ───────────────────────────── GUI App ───────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # ── Window ──
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("920x720")
        self.minsize(780, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # ── State ──
        cfg = load_config()
        self.url_var = StringVar(value="")
        self.browser_var = StringVar(value=cfg.get("browser", "chrome"))
        self.use_cookie_file_var = BooleanVar(value=cfg.get("use_cookie_file", False))
        self.cookie_file_var = StringVar(value=cfg.get("cookie_file", ""))
        self.quality_var = StringVar(value=cfg.get("quality", "Best (Auto)"))
        self.player_var = StringVar(value=cfg.get("player", "(Built-in yt-dlp)"))
        self.download_dir_var = StringVar(value=cfg.get("download_dir", DEFAULT_DOWNLOAD_DIR))
        self.playlist_var = BooleanVar(value=cfg.get("playlist", False))
        self.is_busy = False
        self._current_info: dict | None = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ───────── UI Construction ─────────

    def _build_ui(self):
        # Title banner
        banner = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=0)
        banner.pack(fill="x")
        ctk.CTkLabel(
            banner, text=f"🎬  {APP_NAME}", font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#e94560",
        ).pack(side="left", padx=20, pady=12)
        ctk.CTkLabel(
            banner, text="Play & Download Private / Members-Only YouTube Videos",
            font=ctk.CTkFont(size=12), text_color="#aaaaaa",
        ).pack(side="left", padx=10, pady=12)

        # ── Main Scrollable ──
        main = ctk.CTkScrollableFrame(self)
        main.pack(fill="both", expand=True, padx=12, pady=8)

        # ── Section 1: URL ──
        self._section(main, "📎  Video / Playlist URL")
        url_frame = ctk.CTkFrame(main, fg_color="transparent")
        url_frame.pack(fill="x", padx=10, pady=(0, 8))
        self.url_entry = ctk.CTkEntry(url_frame, textvariable=self.url_var,
                                       placeholder_text="https://www.youtube.com/watch?v=...",
                                       height=38, font=ctk.CTkFont(size=13))
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(url_frame, text="Paste", width=70, command=self._paste_url).pack(side="left", padx=2)
        ctk.CTkButton(url_frame, text="Fetch Info", width=90, fg_color="#0f3460",
                       command=self._fetch_info).pack(side="left", padx=2)

        # ── Info display ──
        self.info_label = ctk.CTkLabel(main, text="", wraplength=800, justify="left",
                                        font=ctk.CTkFont(size=12), text_color="#cccccc")
        self.info_label.pack(fill="x", padx=16, pady=(0, 6))

        # ── Section 2: Authentication ──
        self._section(main, "🔑  Authentication (Your Cookies)")
        auth_frame = ctk.CTkFrame(main, fg_color="transparent")
        auth_frame.pack(fill="x", padx=10, pady=(0, 8))

        # Browser cookies
        ctk.CTkLabel(auth_frame, text="Browser:", font=ctk.CTkFont(size=12)).grid(
            row=0, column=0, padx=6, pady=4, sticky="w")
        ctk.CTkOptionMenu(auth_frame, variable=self.browser_var, values=BROWSERS,
                           width=140).grid(row=0, column=1, padx=6, pady=4, sticky="w")
        ctk.CTkLabel(auth_frame, text="(reads cookies from your logged-in browser)",
                      font=ctk.CTkFont(size=11), text_color="#888888").grid(
            row=0, column=2, padx=6, pady=4, sticky="w")

        # Or cookie file
        self.cookie_file_cb = ctk.CTkCheckBox(auth_frame, text="Use cookies.txt file instead",
                                               variable=self.use_cookie_file_var,
                                               command=self._toggle_cookie_file)
        self.cookie_file_cb.grid(row=1, column=0, columnspan=2, padx=6, pady=4, sticky="w")

        self.cookie_file_entry = ctk.CTkEntry(auth_frame, textvariable=self.cookie_file_var,
                                               placeholder_text="Path to cookies.txt",
                                               width=340, state="disabled")
        self.cookie_file_entry.grid(row=2, column=0, columnspan=2, padx=6, pady=4, sticky="w")
        self.cookie_browse_btn = ctk.CTkButton(auth_frame, text="Browse", width=70,
                                                state="disabled", command=self._browse_cookie_file)
        self.cookie_browse_btn.grid(row=2, column=2, padx=6, pady=4, sticky="w")

        # ── Section 3: Quality & Player ──
        self._section(main, "⚙️  Quality & Playback")
        qp_frame = ctk.CTkFrame(main, fg_color="transparent")
        qp_frame.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(qp_frame, text="Quality:", font=ctk.CTkFont(size=12)).grid(
            row=0, column=0, padx=6, pady=4, sticky="w")
        ctk.CTkOptionMenu(qp_frame, variable=self.quality_var,
                           values=list(QUALITY_OPTIONS.keys()), width=180).grid(
            row=0, column=1, padx=6, pady=4, sticky="w")

        ctk.CTkLabel(qp_frame, text="Player:", font=ctk.CTkFont(size=12)).grid(
            row=0, column=2, padx=(20, 6), pady=4, sticky="w")
        ctk.CTkOptionMenu(qp_frame, variable=self.player_var,
                           values=PLAYERS, width=180).grid(
            row=0, column=3, padx=6, pady=4, sticky="w")

        ctk.CTkCheckBox(qp_frame, text="Download full playlist (if URL is a playlist)",
                         variable=self.playlist_var).grid(
            row=1, column=0, columnspan=3, padx=6, pady=4, sticky="w")

        # ── Section 4: Download Dir ──
        self._section(main, "📂  Download Location")
        dir_frame = ctk.CTkFrame(main, fg_color="transparent")
        dir_frame.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkEntry(dir_frame, textvariable=self.download_dir_var, width=500,
                      font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(dir_frame, text="Browse", width=70,
                       command=self._browse_download_dir).pack(side="left")

        # ── Action Buttons ──
        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.pack(fill="x", padx=10, pady=14)

        self.play_btn = ctk.CTkButton(actions, text="▶  Play / Stream", width=200,
                                       height=44, font=ctk.CTkFont(size=15, weight="bold"),
                                       fg_color="#e94560", hover_color="#c81e45",
                                       command=self._play)
        self.play_btn.pack(side="left", padx=8)

        self.dl_btn = ctk.CTkButton(actions, text="⬇  Download", width=200,
                                     height=44, font=ctk.CTkFont(size=15, weight="bold"),
                                     fg_color="#0f3460", hover_color="#0a2647",
                                     command=self._download)
        self.dl_btn.pack(side="left", padx=8)

        self.open_dir_btn = ctk.CTkButton(actions, text="📁  Open Folder", width=130,
                                           height=44, command=self._open_download_dir)
        self.open_dir_btn.pack(side="left", padx=8)

        # ── Progress ──
        self._section(main, "📊  Progress")
        self.progress_bar = ctk.CTkProgressBar(main, width=600)
        self.progress_bar.pack(padx=16, pady=(0, 4))
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(main, text="Ready", font=ctk.CTkFont(size=12),
                                          text_color="#aaaaaa", wraplength=800, justify="left")
        self.status_label.pack(fill="x", padx=16, pady=(0, 4))

        # ── Log ──
        self._section(main, "📋  Log")
        self.log_box = ctk.CTkTextbox(main, height=120, font=ctk.CTkFont(family="Consolas", size=11),
                                       state="normal", wrap="word")
        self.log_box.pack(fill="x", padx=10, pady=(0, 10))

        # Initialize cookie-file toggle
        self._toggle_cookie_file()

    # ───────── Helpers ─────────

    @staticmethod
    def _section(parent, title: str):
        ctk.CTkLabel(parent, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                      text_color="#e94560").pack(anchor="w", padx=10, pady=(12, 2))

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert(END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_box.see(END)

    def _set_status(self, msg: str):
        self.status_label.configure(text=msg)

    def _set_busy(self, busy: bool):
        self.is_busy = busy
        state = "disabled" if busy else "normal"
        self.play_btn.configure(state=state)
        self.dl_btn.configure(state=state)

    def _get_auth_args(self) -> dict:
        """Return the correct auth kwargs for YTEngine calls."""
        if self.use_cookie_file_var.get() and self.cookie_file_var.get():
            return {"cookies_file": self.cookie_file_var.get()}
        return {"cookies_from_browser": self.browser_var.get()}

    def _get_quality_format(self) -> str:
        return QUALITY_OPTIONS.get(self.quality_var.get(), "bv*+ba/b")

    def _paste_url(self):
        try:
            text = self.clipboard_get()
            self.url_var.set(text.strip())
        except Exception:
            pass

    def _browse_cookie_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.cookie_file_var.set(path)

    def _browse_download_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.download_dir_var.set(path)

    def _open_download_dir(self):
        d = self.download_dir_var.get()
        os.makedirs(d, exist_ok=True)
        if os.name == "nt":
            os.startfile(d)
        else:
            subprocess.Popen(["xdg-open", d])

    def _toggle_cookie_file(self):
        if self.use_cookie_file_var.get():
            self.cookie_file_entry.configure(state="normal")
            self.cookie_browse_btn.configure(state="normal")
        else:
            self.cookie_file_entry.configure(state="disabled")
            self.cookie_browse_btn.configure(state="disabled")

    def _save_prefs(self):
        save_config({
            "browser": self.browser_var.get(),
            "use_cookie_file": self.use_cookie_file_var.get(),
            "cookie_file": self.cookie_file_var.get(),
            "quality": self.quality_var.get(),
            "player": self.player_var.get(),
            "download_dir": self.download_dir_var.get(),
            "playlist": self.playlist_var.get(),
        })

    def _on_close(self):
        self._save_prefs()
        self.destroy()

    # ───────── Actions ─────────

    def _validate_url(self) -> str | None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please enter a YouTube video URL.")
            return None
        if not re.search(r"(youtube\.com|youtu\.be)", url):
            messagebox.showwarning("Invalid URL", "This doesn't look like a YouTube URL.")
            return None
        return url

    def _fetch_info(self):
        url = self._validate_url()
        if not url:
            return

        def _worker():
            self._set_busy(True)
            self._set_status("Fetching video info...")
            self._log(f"Fetching info for: {url}")
            auth = self._get_auth_args()
            info = YTEngine.fetch_info(url, **auth)
            if info and "error" not in info:
                title = info.get("title", "Unknown")
                uploader = info.get("uploader", "Unknown")
                duration = info.get("duration_string") or info.get("duration", "?")
                availability = info.get("availability", "public")
                view_count = info.get("view_count", "N/A")
                desc = (
                    f"✅ Title: {title}\n"
                    f"👤 Uploader: {uploader}\n"
                    f"⏱ Duration: {duration}\n"
                    f"👁 Views: {view_count}\n"
                    f"🔒 Availability: {availability}"
                )
                self.info_label.configure(text=desc)
                self._log(f"Info fetched: {title} ({availability})")
                self._current_info = info
            else:
                err = info.get("error", "Unknown error") if info else "Failed to fetch info"
                self.info_label.configure(text=f"❌ Error: {err}")
                self._log(f"Error: {err}")
            self._set_status("Ready")
            self._set_busy(False)

        threading.Thread(target=_worker, daemon=True).start()

    def _play(self):
        url = self._validate_url()
        if not url:
            return

        player = self.player_var.get()
        auth = self._get_auth_args()

        def _worker():
            self._set_busy(True)
            if player == "(Built-in yt-dlp)":
                # Stream via yt-dlp with mpv fallback, then vlc fallback
                self._set_status("Trying to stream with mpv...")
                self._log(f"Attempting playback with mpv for: {url}")
                result = YTEngine.stream_in_player(url, player="mpv", **auth)
                if result.get("status") == "error":
                    self._log(f"mpv failed: {result.get('error')}. Trying vlc...")
                    self._set_status("Trying to stream with vlc...")
                    result = YTEngine.stream_in_player(url, player="vlc", **auth)
                    if result.get("status") == "error":
                        self._log(f"vlc failed: {result.get('error')}. Downloading and opening instead...")
                        self._set_status("Falling back to download-then-open...")
                        self._download_and_open(url, auth)
                        self._set_busy(False)
                        return
                self._log("Player launched successfully!")
                self._set_status("Playing...")
            else:
                self._set_status(f"Streaming with {player}...")
                self._log(f"Streaming with {player}: {url}")
                result = YTEngine.stream_in_player(url, player=player.lower(), **auth)
                if result.get("status") == "error":
                    err = result.get("error", "Unknown")
                    self._log(f"Error: {err}")
                    messagebox.showerror("Playback Error", err)
                    self._set_status("Ready")
                else:
                    self._log("Player launched successfully!")
                    self._set_status("Playing...")
            self._set_busy(False)

        threading.Thread(target=_worker, daemon=True).start()

    def _download_and_open(self, url: str, auth: dict):
        """Fallback: download the video then open it with the system default player."""
        quality = self._get_quality_format()
        dl_dir = self.download_dir_var.get()
        os.makedirs(dl_dir, exist_ok=True)

        def _hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total:
                    pct = downloaded / total
                    self.progress_bar.set(pct)
                    self._set_status(f"Downloading... {pct * 100:.1f}%")
            elif d["status"] == "finished":
                self.progress_bar.set(1.0)
                self._set_status("Download complete! Opening...")
                filepath = d.get("filename")
                if filepath and os.path.exists(filepath):
                    if os.name == "nt":
                        os.startfile(filepath)
                    else:
                        subprocess.Popen(["xdg-open", filepath])

        result = YTEngine.download(
            url, quality=quality, output_dir=dl_dir, progress_hook=_hook, **auth)
        if result["status"] == "error":
            self._log(f"Download error: {result.get('error')}")
            self._set_status("Error during download")
        else:
            self._log("Downloaded and opened.")
            self._set_status("Ready")

    def _download(self):
        url = self._validate_url()
        if not url:
            return

        quality_label = self.quality_var.get()
        quality = self._get_quality_format()
        audio_only = "Audio Only" in quality_label
        dl_dir = self.download_dir_var.get()
        os.makedirs(dl_dir, exist_ok=True)
        auth = self._get_auth_args()

        def _hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total:
                    pct = downloaded / total
                    self.progress_bar.set(pct)
                    self._set_status(
                        f"Downloading... {pct * 100:.1f}%  "
                        f"({downloaded / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB)"
                    )
                speed = d.get("speed")
                if speed:
                    self._set_status(
                        self.status_label.cget("text") + f"  @ {speed / 1024 / 1024:.1f} MB/s"
                    )
            elif d["status"] == "finished":
                self.progress_bar.set(1.0)
                self._set_status("Merging / Post-processing...")

        def _worker():
            self._set_busy(True)
            self.progress_bar.set(0)
            self._log(f"Downloading ({quality_label}): {url}")
            self._set_status("Starting download...")

            result = YTEngine.download(
                url, quality=quality, output_dir=dl_dir,
                audio_only=audio_only, progress_hook=_hook, **auth)

            if result["status"] == "ok":
                info = result.get("info", {})
                title = info.get("title", "video") if info else "video"
                self._log(f"✅ Downloaded: {title}")
                self._set_status(f"✅ Done! Saved to {dl_dir}")
                self.progress_bar.set(1.0)
            else:
                err = result.get("error", "Unknown error")
                self._log(f"❌ Error: {err}")
                self._set_status(f"❌ Error: {err}")
                self.progress_bar.set(0)

            self._set_busy(False)

        threading.Thread(target=_worker, daemon=True).start()


# ───────────────────────────── Entry Point ───────────────────────────

def main():
    # Ensure default download dir exists
    os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
