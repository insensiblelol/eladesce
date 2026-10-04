"""
Studio Pro – Ultimate Desktop Video Suite v3.5 (Master Edition)
Norsk, superrask, moderne – Profesjonelt videoverktøy med FFmpeg, yt-dlp og OpenCV.
"""

import sys
import os
import re
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from datetime import datetime

import cv2
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QGridLayout, QHBoxLayout,
    QTabWidget, QPushButton, QLabel, QLineEdit, QComboBox, QDoubleSpinBox,
    QSpinBox, QSlider, QListWidget, QListWidgetItem, QProgressBar, QMessageBox,
    QFileDialog, QMenu, QScrollArea, QGroupBox, QSplitter, QCheckBox, QDialog,
    QFormLayout, QFrame, QSizePolicy, QToolTip, QStyle, QTextEdit
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QUrl, QTime, QSize, QPoint, QRect, QSettings, QObject
)
from PyQt6.QtGui import (
    QIcon, QFont, QColor, QPalette, QKeySequence, QAction, QCursor,
    QImage, QPixmap, QDragEnterEvent, QDropEvent
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget


def get_ffmpeg_exe():
    """Detects available FFmpeg executable (bundled, PATH, or imageio-ffmpeg)."""
    local = Path(__file__).resolve().parent / "ffmpeg.exe"
    if local.is_file():
        return str(local)
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg and os.path.isfile(which_ffmpeg):
        return which_ffmpeg
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and os.path.isfile(bundled):
            return bundled
    except Exception:
        pass
    return None


def ffmpeg_is_usable():
    path = get_ffmpeg_exe()
    return bool(path and os.path.isfile(path))


def get_js_runtimes_for_ytdlp():
    """Return explicitly available JavaScript runtimes for yt-dlp EJS."""
    runtimes = {}
    # Deno is the recommended runtime; Node and QuickJS are useful fallbacks.
    for name in ("deno", "node", "quickjs", "qjs", "bun"):
        if shutil.which(name):
            key = "quickjs" if name == "qjs" else name
            runtimes[key] = {}
    return runtimes


def safe_output_stem(name: str, fallback: str = "studio_pro_klipp") -> str:
    """Create a Windows-safe, readable output filename stem."""
    value = (name or "").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = fallback
    # Windows device names are not safe as standalone filenames.
    if value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL"}:
        value = f"_{value}_"
    return value[:180]


def build_atempo_chain(speed: float) -> list:
    """Build valid FFmpeg atempo filters for any practical playback speed."""
    speed = max(0.05, float(speed))
    filters = []
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed /= 0.5
    while speed > 2.0:
        filters.append("atempo=2.0")
        speed /= 2.0
    filters.append(f"atempo={speed:.6f}")
    return filters


def escape_drawtext(text: str) -> str:
    """Escape common FFmpeg drawtext filter separators."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("%", "\\%")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def build_ytdlp_options(
    out_dir, fmt_idx, cookie_idx, custom_cookie_file=None, prefer_exported_cookies=True,
    write_subs=False, embed_subs=False, embed_thumbnail=False,
):
    """Build yt-dlp options dict used by the in-process downloader."""
    ffmpeg_bin = get_ffmpeg_exe()
    opts = {
        "outtmpl": os.path.join(out_dir, "%(title).200B.%(ext)s"),
        "nocheckcertificate": True,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "merge_output_format": "mp4",
        "noprogress": True,
        "quiet": True,
        "continuedl": True,
        "overwrites": False,
        "noplaylist": False,
    }
    if ffmpeg_bin:
        opts["ffmpeg_location"] = ffmpeg_bin

    js_r = get_js_runtimes_for_ytdlp()
    if js_r:
        opts["js_runtimes"] = js_r
    else:
        opts["remote_components"] = ["ejs:github"]

    cookie_path = resolve_youtube_cookie_file(cookie_idx, custom_cookie_file, prefer_exported_cookies)
    if cookie_path:
        opts["cookiefile"] = cookie_path
    elif cookie_idx in BROWSER_COOKIE_MAP:
        opts["cookiesfrombrowser"] = (BROWSER_COOKIE_MAP[cookie_idx],)

    if write_subs:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["en", "no", "nb", "nn", "de", "fr", "es", ".*"]
        if embed_subs:
            opts["embedsubtitles"] = True
    if embed_thumbnail:
        opts["writethumbnail"] = True
        opts["embedthumbnail"] = True

    if fmt_idx == 0:
        opts["format"] = "b/bestvideo+bestaudio/best"
    elif fmt_idx == 1:
        opts["format"] = "bv*[height<=1080]+ba/b/best"
    elif fmt_idx == 2:
        opts["format"] = "bv*[height<=720]+ba/b/best"
    else:
        audio_exts = ["mp3", "flac", "wav", "m4a", "opus"]
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_exts[fmt_idx - 3],
            "preferredquality": "0",
        }]
    return opts


MEDIA_EXTENSIONS = {
    ".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".gif",
    ".mp3", ".wav", ".ogg", ".opus", ".flac", ".m4a", ".aac",
}

APP_DIR = Path(__file__).resolve().parent
COOKIES_DIR = APP_DIR / "cookies"
DEFAULT_SANGER_DIR = APP_DIR / "sanger"

BROWSER_COOKIE_MAP = {1: "chrome", 2: "edge", 3: "firefox", 4: "brave", 5: "opera"}


def ensure_app_dirs():
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_SANGER_DIR.mkdir(parents=True, exist_ok=True)


def spotdl_output_template(out_dir: str) -> str:
    """spotdl requires a filename template, not a bare folder path."""
    folder = os.path.abspath(out_dir)
    return os.path.join(folder, "{artists} - {title}.{output-ext}")


def snapshot_media_files(folder: str) -> dict:
    """Return mapping path -> mtime for media files in folder (non-recursive)."""
    result = {}
    if not folder or not os.path.isdir(folder):
        return result
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and Path(path).suffix.lower() in MEDIA_EXTENSIONS:
            result[path] = os.path.getmtime(path)
    return result


def diff_new_media(before: dict, after: dict) -> list:
    new_paths = []
    for path, mtime in after.items():
        if path not in before or after[path] > before[path] + 0.01:
            new_paths.append(path)
    return sorted(new_paths, key=lambda p: after[p], reverse=True)


def resolve_youtube_cookie_file(cookie_idx, custom_cookie_file, prefer_exported=True):
    """
    Resolve Netscape cookies.txt for yt-dlp / spotdl.
    prefer_exported: use cookies/youtube_active.txt when combo is «Ingen».
    """
    if cookie_idx == 6 and custom_cookie_file and os.path.isfile(custom_cookie_file):
        return custom_cookie_file
    if cookie_idx in BROWSER_COOKIE_MAP and prefer_exported:
        active = COOKIES_DIR / "youtube_active.txt"
        if active.is_file() and active.stat().st_size > 64:
            return str(active)
        browser = BROWSER_COOKIE_MAP[cookie_idx]
        exported = COOKIES_DIR / f"youtube_{browser}.txt"
        if exported.is_file() and exported.stat().st_size > 64:
            return str(exported)
    if prefer_exported:
        active = COOKIES_DIR / "youtube_active.txt"
        if active.is_file() and active.stat().st_size > 64:
            return str(active)
    return None


def build_ytdlp_cookie_cli_args(cookie_idx, custom_cookie_file, prefer_exported=True):
    """Extra CLI args for spotdl --yt-dlp-args."""
    path = resolve_youtube_cookie_file(cookie_idx, custom_cookie_file, prefer_exported)
    if path:
        return f'--cookies "{path}"'
    if cookie_idx in BROWSER_COOKIE_MAP:
        return f'--cookies-from-browser {BROWSER_COOKIE_MAP[cookie_idx]}'
    return ""


def parse_url_lines(text: str) -> list:
    urls = []
    for raw in text.replace(",", "\n").splitlines():
        u = raw.strip()
        if not u or u.startswith("#"):
            continue
        if u.startswith("http://") or u.startswith("https://") or u.startswith("spotify:"):
            urls.append(u)
    return urls


def subprocess_flags_hide_window():
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NO_WINDOW


class CookieExportWorker(QThread):
    """One-click export of YouTube cookies from a browser into cookies/youtube_*.txt."""
    status = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, browser: str):
        super().__init__()
        self.browser = browser

    def run(self):
        ensure_app_dirs()
        out_path = COOKIES_DIR / f"youtube_{self.browser}.txt"
        active_path = COOKIES_DIR / "youtube_active.txt"
        self.status.emit(f"🍪 Eksporterer kapsler fra {self.browser} …")

        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--cookies-from-browser", self.browser,
            "--cookies", str(out_path),
            "--skip-download",
            "https://www.youtube.com/",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                creationflags=subprocess_flags_hide_window(),
            )
            log = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if out_path.is_file() and out_path.stat().st_size > 64:
                shutil.copyfile(out_path, active_path)
                self.finished.emit(
                    True,
                    f"Kapsler lagret:\n{out_path}\n\nAktiv fil: {active_path}\n\n"
                    "YouTube-nedlastinger bruker denne automatisk.",
                )
                return
            err = log.strip() or f"Exit code {proc.returncode}"
            if "dpapi" in err.lower() or "decrypt" in err.lower():
                err += (
                    "\n\n💡 Lukk nettleseren helt (Chrome/Edge) og prøv igjen. "
                    "Alternativt: logg inn på youtube.com i nettleseren først."
                )
            self.finished.emit(False, err)
        except Exception as e:
            self.finished.emit(False, str(e))


class RenderWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, cmd, output_path, total_duration_s=0):
        super().__init__()
        self.cmd = cmd
        self.output_path = output_path
        self.total_duration_s = max(0.1, total_duration_s)
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            cmd_with_progress = list(self.cmd)
            if "-progress" not in cmd_with_progress:
                cmd_with_progress.insert(-1, "-progress")
                cmd_with_progress.insert(-1, "pipe:1")

            self.status.emit("⚡ Starter rendering med FFmpeg ...")

            process = subprocess.Popen(
                cmd_with_progress,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                startupinfo=startupinfo,
                bufsize=1,
                universal_newlines=True
            )

            time_pattern = re.compile(r"out_time_ms=(\d+)")
            progress_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

            log_lines = []
            while process.poll() is None:
                if self._is_cancelled:
                    process.terminate()
                    self.finished.emit(False, "Rendering ble avbrutt av brukeren.")
                    return

                line = process.stdout.readline()
                if not line:
                    continue
                log_lines.append(line.strip())

                ms_match = time_pattern.search(line)
                if ms_match:
                    ms_val = int(ms_match.group(1))
                    sec_val = ms_val / 1000000.0
                    pct = int((sec_val / self.total_duration_s) * 100)
                    pct = max(0, min(99, pct))
                    self.progress.emit(pct)
                    self.status.emit(f"⚡ Rendrer: {pct}% fullført ({sec_val:.1f}s / {self.total_duration_s:.1f}s)")
                else:
                    t_match = progress_pattern.search(line)
                    if t_match:
                        h, m, s = float(t_match.group(1)), float(t_match.group(2)), float(t_match.group(3))
                        sec_val = h * 3600 + m * 60 + s
                        pct = int((sec_val / self.total_duration_s) * 100)
                        pct = max(0, min(99, pct))
                        self.progress.emit(pct)
                        self.status.emit(f"⚡ Rendrer: {pct}% fullført ({sec_val:.1f}s / {self.total_duration_s:.1f}s)")

            returncode = process.wait()

            if returncode == 0:
                self.progress.emit(100)
                self.finished.emit(True, self.output_path)
            else:
                err_summary = "\n".join(log_lines[-10:]) if log_lines else "Ukjent feil"
                self.finished.emit(False, f"FFmpeg feilet (returkode {returncode}):\n\n{err_summary}")

        except Exception as e:
            self.finished.emit(False, f"Unntak under rendering: {str(e)}")


class YtDlpDownloadWorker(QThread):
    """Downloads via the yt-dlp Python API (works even when yt-dlp.exe is not on PATH)."""
    status = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(
        self, url, out_dir, fmt_idx, cookie_idx, custom_cookie_file=None,
        prefer_exported_cookies=True, write_subs=False, embed_subs=False, embed_thumbnail=False,
    ):
        super().__init__()
        self.url = url.strip()
        self.out_dir = out_dir
        self.fmt_idx = fmt_idx
        self.cookie_idx = cookie_idx
        self.custom_cookie_file = custom_cookie_file
        self.prefer_exported_cookies = prefer_exported_cookies
        self.write_subs = write_subs
        self.embed_subs = embed_subs
        self.embed_thumbnail = embed_thumbnail
        self._cancelled = False
        self._last_filepath = ""

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import yt_dlp
        except ImportError:
            self.finished.emit(
                False,
                "yt-dlp er ikke installert i dette Python-miljøet.\n\n"
                f"Kjør i terminal:\n{sys.executable} -m pip install -U yt-dlp imageio-ffmpeg",
            )
            return

        if self.fmt_idx >= 3 and not ffmpeg_is_usable():
            self.finished.emit(
                False,
                "FFmpeg mangler – nødvendig for å trekke ut lyd.\n\n"
                f"Kjør: {sys.executable} -m pip install imageio-ffmpeg\n"
                "eller legg ffmpeg.exe i app-mappen.",
            )
            return

        opts = build_ytdlp_options(
            self.out_dir, self.fmt_idx, self.cookie_idx, self.custom_cookie_file,
            prefer_exported_cookies=self.prefer_exported_cookies,
            write_subs=self.write_subs,
            embed_subs=self.embed_subs,
            embed_thumbnail=self.embed_thumbnail,
        )

        def progress_hook(data):
            if self._cancelled:
                raise yt_dlp.utils.DownloadCancelled("Avbrutt av bruker")
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                done = data.get("downloaded_bytes") or 0
                if total > 0:
                    pct = max(0, min(99, int(done * 100 / total)))
                    self.progress.emit(pct)
                    eta = (data.get("_eta_str") or "").strip()
                    self.status.emit(f"📥 Laster ned: {pct}%{f' (ETA {eta})' if eta else ''}")
            elif data.get("status") == "finished":
                path = data.get("filename") or data.get("info_dict", {}).get("_filename", "")
                if path:
                    self._last_filepath = path
                self.progress.emit(100)

        opts["progress_hooks"] = [progress_hook]

        self.status.emit("⏳ Henter metadata og starter nedlasting …")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([self.url])
            if self._last_filepath and os.path.isfile(self._last_filepath):
                self.finished.emit(True, self._last_filepath)
            else:
                self.finished.emit(True, self.out_dir)
        except yt_dlp.utils.DownloadCancelled:
            self.finished.emit(False, "Nedlasting ble avbrutt.")
        except Exception as e:
            self.finished.emit(False, str(e))


class MediaInfoWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, url, cookie_idx, custom_cookie_file=None):
        super().__init__()
        self.url = url.strip()
        self.cookie_idx = cookie_idx
        self.custom_cookie_file = custom_cookie_file

    def run(self):
        try:
            import yt_dlp
        except ImportError:
            self.finished.emit(False, "yt-dlp mangler (pip install yt-dlp)")
            return
        opts = build_ytdlp_options(str(Path(__file__).parent / "sanger"), 0, self.cookie_idx, self.custom_cookie_file)
        opts["simulate"] = True
        opts["quiet"] = True
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
            if not info:
                self.finished.emit(False, "Fant ingen informasjon for denne lenken.")
                return
            title = info.get("title") or "Ukjent tittel"
            duration = info.get("duration")
            dur_txt = f"{duration // 60}:{duration % 60:02d}" if duration else "?"
            uploader = info.get("uploader") or info.get("channel") or "?"
            ext = info.get("ext") or "?"
            lines = [
                f"🎬 {title}",
                f"👤 {uploader}",
                f"⏱ {dur_txt}  ·  format: {ext}",
            ]
            pl_count = info.get("playlist_count")
            if pl_count:
                lines.append(f"📋 Spilleliste: {pl_count} elementer (alle lastes ned ved «Start Nedlasting»)")
            self.finished.emit(True, "\n".join(lines))
        except Exception as e:
            self.finished.emit(False, str(e))


class SpotifyDownloadWorker(QThread):
    status = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(
        self, url, output_dir, audio_format="mp3",
        cookie_idx=0, custom_cookie_file=None, prefer_exported_cookies=True,
    ):
        super().__init__()
        self.url = url
        self.output_dir = os.path.abspath(output_dir)
        self.audio_format = audio_format
        self.cookie_idx = cookie_idx
        self.custom_cookie_file = custom_cookie_file
        self.prefer_exported_cookies = prefer_exported_cookies
        self._cancelled = False
        self._process = None

    def cancel(self):
        self._cancelled = True
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def run(self):
        try:
            try:
                import spotdl  # noqa: F401
                spotdl_ok = True
            except ImportError:
                spotdl_ok = shutil.which("spotdl") is not None
            if not spotdl_ok:
                self.finished.emit(
                    False,
                    "spotdl er ikke installert.\n\n"
                    f"{sys.executable} -m pip install -U spotdl",
                )
                return

            os.makedirs(self.output_dir, exist_ok=True)
            before = snapshot_media_files(self.output_dir)

            self.status.emit(f"⏳ Henter fra Spotify ({self.audio_format}) …")
            ffmpeg_bin = get_ffmpeg_exe()
            if not ffmpeg_bin:
                self.finished.emit(False, "FFmpeg mangler (pip install imageio-ffmpeg).")
                return

            output_template = spotdl_output_template(self.output_dir)
            cmd = [
                sys.executable, "-m", "spotdl", "download", self.url,
                "--ffmpeg", ffmpeg_bin,
                "--output", output_template,
                "--format", self.audio_format,
                "--overwrite", "force",
                "--print-errors",
            ]
            yt_args = build_ytdlp_cookie_cli_args(
                self.cookie_idx, self.custom_cookie_file, self.prefer_exported_cookies,
            )
            if yt_args:
                cmd += ["--yt-dlp-args", yt_args]

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.output_dir,
                creationflags=subprocess_flags_hide_window(),
                bufsize=1,
                universal_newlines=True,
            )

            pct_re = re.compile(r"(\d+)%")
            saved_re = re.compile(r"(?:Downloaded|Saved|Skipping)\s+[\"']?(.+?)[\"']?\s*$", re.I)
            log_lines = []
            last_saved = ""

            while self._process.poll() is None:
                if self._cancelled:
                    self._process.terminate()
                    self.finished.emit(False, "Spotify-nedlasting avbrutt.")
                    return
                line = self._process.stdout.readline()
                if not line:
                    continue
                line = line.strip()
                log_lines.append(line)
                sm = saved_re.search(line)
                if sm:
                    last_saved = sm.group(1)
                match = pct_re.search(line)
                if match:
                    pct = int(match.group(1))
                    self.progress.emit(pct)
                    self.status.emit(f"🎧 Spotify: {pct}%")
                elif "download" in line.lower():
                    self.status.emit(f"🎧 {line[:120]}")

            returncode = self._process.wait()
            after = snapshot_media_files(self.output_dir)
            new_files = diff_new_media(before, after)

            if new_files:
                self.progress.emit(100)
                self.finished.emit(True, new_files[0])
                return

            if returncode == 0:
                candidate = ""
                if last_saved:
                    p = last_saved if os.path.isabs(last_saved) else os.path.join(self.output_dir, last_saved)
                    if os.path.isfile(p):
                        candidate = p
                if not candidate:
                    candidate = self.find_newest_media_in_dir(self.output_dir)
                if candidate:
                    self.progress.emit(100)
                    self.finished.emit(True, candidate)
                    return
                self.finished.emit(
                    False,
                    "spotdl rapporterte suksess, men ingen lydfil ble funnet i:\n"
                    f"{self.output_dir}\n\n"
                    "Dette skjer ofte når --output bare var mappenavn (nå rettet til fil-mal).\n"
                    f"Siste logg:\n" + "\n".join(log_lines[-15:]),
                )
                return

            err_summary = "\n".join(log_lines[-15:]) if log_lines else "Ukjent feil"
            self.finished.emit(False, f"Spotify-feil (returkode {returncode}):\n\n{err_summary}")
        except Exception as e:
            self.finished.emit(False, str(e))

    @staticmethod
    def find_newest_media_in_dir(folder):
        best = ""
        best_t = 0
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and Path(path).suffix.lower() in MEDIA_EXTENSIONS:
                t = os.path.getmtime(path)
                if t > best_t:
                    best_t = t
                    best = path
        return best


class ClickOverlay(QWidget):
    """Transparent overlay catching clicks over the video area."""
    single_click_signal = pyqtSignal()
    double_click_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._emit_single_click)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._timer.isActive():
                self._timer.stop()
                self.double_click_signal.emit()
            else:
                self._timer.start(QApplication.doubleClickInterval())

    def _emit_single_click(self):
        self.single_click_signal.emit()


class StudioProMasterSuite(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Studio Pro – Ultimate Desktop Video Suite v3.5")
        self.setMinimumSize(1240, 840)

        # State Variables
        self.video_path = ""
        self.duration_ms = 0
        self.video_width = 1920
        self.video_height = 1080
        self.fps = 30.0
        self.bookmarks_data = {}
        self.muted = False
        self.is_loop_ab = False
        self.last_downloaded_file = ""
        self._download_started_at = 0.0
        self.dl_worker = None
        self._yt_cookie_retry_done = False
        self.download_queue = []
        self._queue_active = False
        self._current_queue_id = None
        self._clipboard_last = ""
        ensure_app_dirs()

        self.normal_geometry = None
        self.normal_flags = None
        self.is_fullscreen_mode = False

        # Settings Storage (before paths that read from settings)
        self.settings = QSettings("StudioPro", "EditorV35")
        self.custom_download_dir = self.settings.value("downloadDir", "")
        if self.custom_download_dir and not os.path.isdir(self.custom_download_dir):
            self.custom_download_dir = ""
        self.custom_cookie_file = self.settings.value("cookieFile", "")

        # Audio & Media Setup
        self.audio_output = QAudioOutput(self)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self.on_player_position_changed)
        self.media_player.durationChanged.connect(self.on_player_duration_changed)

        self.recent_files = self.settings.value("recentFiles", [])
        if isinstance(self.recent_files, str):
            self.recent_files = [self.recent_files]

        self.setup_stylesheet()
        self.setAcceptDrops(True)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self._build_main_shell()

        self.init_editor_tab()
        self.init_downloader_tab()
        self.init_info_tab()
        self._update_page_header(self.tabs.currentIndex())

        self.restore_editor_preferences()
        self.install_app_shortcuts()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        QTimer.singleShot(100, self.check_environment)

        if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            QTimer.singleShot(200, lambda: self.load_target_asset(sys.argv[1]))

    def setup_stylesheet(self):
        """Studio Pro visual system: one accent, three surface levels, consistent spacing."""
        self.setStyleSheet("""
            * { font-family: "Segoe UI", "Inter", sans-serif; font-size: 12px; }

            QMainWindow, QWidget {
                background: #0b0e13;
                color: #e7eaf0;
            }
            QToolTip {
                background: #171b23;
                color: #f8fafc;
                border: 1px solid #303746;
                padding: 6px 8px;
                border-radius: 6px;
            }

            /* ---------- Shell ---------- */
            QFrame#sidebar {
                background: #0f1218;
                border-right: 1px solid #222833;
            }
            QFrame#sidebar QLabel, QFrame#appHeader QLabel { background: transparent; }
            QFrame#appHeader {
                background: #0b0e13;
                border-bottom: 1px solid #222833;
            }
            QLabel#brandTitle { color: #f8fafc; font-size: 17px; font-weight: 800; }
            QLabel#brandTagline { color: #5d6878; font-size: 10px; font-weight: 700; }
            QLabel#pageTitle { color: #f8fafc; font-size: 18px; font-weight: 800; }
            QLabel#pageSubtitle { color: #7a8596; font-size: 12px; }
            QLabel#headerState {
                background: #131821;
                color: #aab4c4;
                border: 1px solid #2a313e;
                border-radius: 12px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#sidebarSection {
                color: #566070;
                font-size: 10px;
                font-weight: 800;
                padding: 4px 18px;
            }
            QLabel#sidebarFooter { color: #566070; font-size: 10px; padding: 0px 18px; }

            QPushButton#navBtn {
                background: transparent;
                color: #8c97a8;
                border: none;
                border-left: 3px solid transparent;
                border-radius: 0px;
                text-align: left;
                padding: 10px 12px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#navBtn:hover { background: #151a22; color: #f2f5fa; }
            QPushButton#navBtn:checked {
                background: #171d27;
                color: #ffffff;
                border-left: 3px solid #536dff;
            }
            QPushButton#headerBtn {
                background: transparent;
                color: #9ea8b7;
                border: 1px solid #292f3a;
                border-radius: 8px;
                padding: 7px 12px;
                font-weight: 600;
            }
            QPushButton#headerBtn:hover { background: #151a22; color: #ffffff; border-color: #3a4454; }

            /* Main page switcher: its tab bar is hidden, the sidebar is the navigation. */
            QTabWidget#pageStack::pane { border: none; background: #0b0e13; }

            /* ---------- Cards / panels ---------- */
            QFrame#card {
                background: #11151c;
                border: 1px solid #222833;
                border-radius: 12px;
            }
            QFrame#card QLabel, QFrame#card QCheckBox { background: transparent; }
            QFrame#card QLineEdit, QFrame#card QComboBox, QFrame#card QSpinBox,
            QFrame#card QDoubleSpinBox, QFrame#card QTextEdit, QFrame#card QListWidget {
                background: #0c1016;
            }
            QLabel#cardTitle { color: #f1f4f9; font-size: 13px; font-weight: 700; }
            QLabel#cardHint { color: #7a8596; font-size: 11px; }
            QLabel#fieldLabel { color: #8f9bad; font-size: 11px; font-weight: 600; }
            QLabel#sectionLabel {
                color: #6b7686;
                font-size: 10px;
                font-weight: 800;
                padding-top: 6px;
            }
            QLabel#valueLabel { color: #c9d1dd; font-size: 11px; font-weight: 600; }
            QLabel#mutedText { color: #8a95a6; font-size: 11px; }
            QLabel#statusText { color: #9fb0c6; font-size: 12px; }
            QLabel#fileBadge {
                background: #11151c;
                color: #aab4c4;
                border: 1px solid #222833;
                border-radius: 8px;
                padding: 7px 12px;
            }
            QLabel#previewBox {
                background: #0c1016;
                color: #cbd5e1;
                border: 1px solid #222833;
                border-radius: 8px;
                padding: 10px 12px;
            }
            QLabel#metaBox {
                background: #0c1016;
                color: #dbe2ec;
                border: 1px solid #222833;
                border-radius: 8px;
                padding: 14px;
                font-family: "Cascadia Mono", "Consolas", monospace;
            }
            QLabel#kbd {
                background: #1a202a;
                color: #e7ebf2;
                border: 1px solid #2f3746;
                border-bottom: 2px solid #2f3746;
                border-radius: 5px;
                padding: 3px 8px;
                font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 11px;
            }
            QLabel#clipLength { color: #a5b4fc; font-weight: 700; }

            QFrame#videoFrame {
                background: #050608;
                border: 1px solid #222833;
                border-radius: 12px;
            }
            QFrame#transportBar {
                background: #11151c;
                border: 1px solid #222833;
                border-radius: 12px;
            }
            QFrame#transportBar QLabel { background: transparent; }
            QLabel#timecode {
                color: #e7ebf2;
                font-family: "Cascadia Mono", "Consolas", monospace;
                font-weight: 700;
            }
            QLabel#timecodeMuted {
                color: #7a8596;
                font-family: "Cascadia Mono", "Consolas", monospace;
            }
            QFrame#vDivider { background: #262d38; border: none; }

            /* Inspector tabs (editor) */
            QTabWidget#inspectorTabs::pane {
                border: 1px solid #222833;
                border-radius: 12px;
                background: #11151c;
                top: -1px;
            }
            QWidget#inspectorPage { background: #11151c; }
            QWidget#inspectorPage QLabel, QWidget#inspectorPage QCheckBox { background: transparent; }
            QTabWidget#inspectorTabs QTabBar::tab {
                background: transparent;
                color: #8c97a8;
                padding: 8px 14px;
                margin-right: 2px;
                border: 1px solid transparent;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                font-weight: 600;
            }
            QTabWidget#inspectorTabs QTabBar::tab:hover { color: #f2f5fa; }
            QTabWidget#inspectorTabs QTabBar::tab:selected {
                background: #11151c;
                color: #ffffff;
                border: 1px solid #222833;
                border-bottom-color: #11151c;
            }

            /* ---------- Controls ---------- */
            QPushButton {
                background: #1a202a;
                color: #dce2eb;
                border: 1px solid #2a313d;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #202733; border-color: #3a4556; color: #ffffff; }
            QPushButton:pressed { background: #161b23; }
            QPushButton:disabled { background: #12161c; color: #4e5868; border-color: #1d232c; }
            QPushButton#secondaryBtn { background: #151a21; color: #b3bdcb; border: 1px solid #2a313c; }
            QPushButton#secondaryBtn:hover { background: #1d232d; color: #ffffff; border-color: #3b4656; }
            QPushButton#ghostBtn {
                background: transparent;
                color: #9ea8b7;
                border: 1px solid transparent;
                padding: 6px 10px;
            }
            QPushButton#ghostBtn:hover { background: #1a202a; color: #ffffff; }
            QPushButton#ghostBtn:disabled { background: transparent; color: #4e5868; }
            QPushButton#dangerBtn { background: transparent; color: #f2a3a3; border: 1px solid #4a2a2e; }
            QPushButton#dangerBtn:hover { background: #2a1719; color: #ffd0d0; }
            QPushButton#dangerBtn:disabled { color: #4e5868; border-color: #1d232c; }
            QPushButton#accentBtn {
                background: #536dff;
                color: #ffffff;
                border: 1px solid #667dff;
                font-weight: 700;
            }
            QPushButton#accentBtn:hover { background: #6179ff; }
            QPushButton#accentBtn:pressed { background: #475fdf; }
            QPushButton#accentBtn:disabled { background: #222a45; color: #7380b3; border-color: #2a3352; }
            QPushButton#playBtn {
                background: #f2f5fa;
                color: #0b0e13;
                border: none;
                border-radius: 17px;
                min-height: 34px;
                padding: 0px 16px;
                font-weight: 800;
            }
            QPushButton#playBtn:hover { background: #ffffff; }
            QPushButton#playBtn:disabled { background: #262d38; color: #5d6878; }

            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QListWidget {
                background: #0e1218;
                color: #e9edf4;
                border: 1px solid #29303b;
                border-radius: 8px;
                padding: 7px 10px;
                selection-background-color: #536dff;
                selection-color: #ffffff;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {
                border-color: #536dff;
            }
            QLineEdit#urlInput { font-size: 13px; padding: 10px 12px; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView {
                background: #121720;
                color: #eef2f8;
                border: 1px solid #303847;
                selection-background-color: #536dff;
                padding: 4px;
            }
            QListWidget { padding: 4px; }
            QListWidget::item { padding: 7px 8px; margin: 1px 0px; border-radius: 6px; }
            QListWidget::item:hover { background: #171d26; }
            QListWidget::item:selected { background: #212939; color: #ffffff; }

            QSlider { background: transparent; }
            QSlider::groove:horizontal { height: 4px; background: #262d38; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #536dff; border-radius: 2px; }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; margin: -5px 0;
                background: #f2f5fa; border: 2px solid #536dff; border-radius: 7px;
            }
            QSlider::handle:horizontal:disabled { background: #3a4252; border-color: #3a4252; }
            QProgressBar {
                background: #171c24;
                border: none;
                border-radius: 4px;
                min-height: 8px;
                max-height: 8px;
                text-align: center;
                color: transparent;
            }
            QProgressBar::chunk { background: #536dff; border-radius: 4px; }

            QCheckBox { color: #c3ccd8; spacing: 8px; }
            QCheckBox::indicator {
                width: 15px; height: 15px; border-radius: 4px;
                background: #0c1016; border: 1px solid #364051;
            }
            QCheckBox::indicator:hover { border-color: #536dff; }
            QCheckBox::indicator:checked { background: #536dff; border-color: #536dff; }

            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
            QScrollBar::handle:vertical { background: #2b3340; min-height: 28px; border-radius: 4px; }
            QScrollBar::handle:vertical:hover { background: #3a4555; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0px; }
            QSplitter::handle { background: transparent; }
            QSplitter::handle:hover { background: #2d3542; }

            QStatusBar#appStatusBar {
                background: #0a0d12;
                color: #6b7686;
                border-top: 1px solid #1d232c;
                font-size: 11px;
            }
            QMenu { background: #131821; color: #e7ebf2; border: 1px solid #303847; padding: 4px; }
            QMenu::item { padding: 7px 22px 7px 10px; border-radius: 5px; }
            QMenu::item:selected { background: #202838; }
        """)

    # ---------- small layout helpers ----------
    def _card(self, title=None, hint=None, spacing=10):
        """Return (frame, inner_layout) for a titled card panel."""
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(spacing)
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("cardTitle")
            layout.addWidget(lbl)
        if hint:
            h = QLabel(hint)
            h.setObjectName("cardHint")
            h.setWordWrap(True)
            layout.addWidget(h)
        return frame, layout

    def _field_label(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    def _section_label(self, text):
        lbl = QLabel(text.upper())
        lbl.setObjectName("sectionLabel")
        return lbl

    def _vdivider(self):
        line = QFrame()
        line.setObjectName("vDivider")
        line.setFixedSize(1, 22)
        return line

    def _build_main_shell(self):
        """Sidebar navigation + page header + page stack."""
        shell = QWidget()
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- Sidebar ----------
        self.nav_panel = QFrame()
        self.nav_panel.setObjectName("sidebar")
        self.nav_panel.setFixedWidth(208)
        nav = QVBoxLayout(self.nav_panel)
        nav.setContentsMargins(0, 18, 0, 14)
        nav.setSpacing(2)

        brand_box = QVBoxLayout()
        brand_box.setContentsMargins(18, 0, 18, 0)
        brand_box.setSpacing(0)
        brand_title = QLabel("Studio Pro")
        brand_title.setObjectName("brandTitle")
        brand_tagline = QLabel("MEDIA WORKSPACE")
        brand_tagline.setObjectName("brandTagline")
        brand_box.addWidget(brand_title)
        brand_box.addWidget(brand_tagline)
        nav.addLayout(brand_box)
        nav.addSpacing(18)

        actions_box = QVBoxLayout()
        actions_box.setContentsMargins(14, 0, 14, 0)
        actions_box.setSpacing(6)
        self.nav_open_btn = QPushButton("＋  Åpne mediefil")
        self.nav_open_btn.setObjectName("accentBtn")
        self.nav_open_btn.setMinimumHeight(38)
        self.nav_open_btn.setToolTip("Åpne en video- eller lydfil (Ctrl+O)")
        self.nav_open_btn.clicked.connect(self.import_file)
        self.btn_recent = QPushButton("🗂  Nylige filer")
        self.btn_recent.setObjectName("secondaryBtn")
        self.btn_recent.setToolTip("Vis nylig åpnede filer (Ctrl+Shift+O)")
        self.btn_recent.clicked.connect(self.show_recent_files_menu)
        actions_box.addWidget(self.nav_open_btn)
        actions_box.addWidget(self.btn_recent)
        nav.addLayout(actions_box)
        nav.addSpacing(20)

        section = QLabel("ARBEIDSOMRÅDE")
        section.setObjectName("sidebarSection")
        nav.addWidget(section)

        self.nav_buttons = []
        for index, (icon, label, tip) in enumerate((
            ("🎬", "Editor", "Klipp, juster og eksporter  (Ctrl+1)"),
            ("⬇", "Nedlaster", "Last ned fra nettet og bygg en kø  (Ctrl+2)"),
            ("ⓘ", "Info && hjelp", "Fildetaljer og hurtigtaster  (Ctrl+3)"),
        )):
            btn = QPushButton(f"  {icon}   {label}")
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, i=index: self._set_active_page(i))
            nav.addWidget(btn)
            self.nav_buttons.append(btn)

        nav.addStretch()

        footer = QLabel("Alt behandles lokalt på maskinen")
        footer.setObjectName("sidebarFooter")
        footer.setWordWrap(True)
        nav.addWidget(footer)

        root.addWidget(self.nav_panel)

        # ---------- Main column ----------
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.app_header = QFrame()
        self.app_header.setObjectName("appHeader")
        self.app_header.setFixedHeight(64)
        header = QHBoxLayout(self.app_header)
        header.setContentsMargins(24, 8, 18, 8)
        header.setSpacing(8)

        page_text = QVBoxLayout()
        page_text.setSpacing(0)
        self.page_title = QLabel("Editor")
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel("")
        self.page_subtitle.setObjectName("pageSubtitle")
        page_text.addWidget(self.page_title)
        page_text.addWidget(self.page_subtitle)
        header.addLayout(page_text)
        header.addStretch()

        self.lbl_header_state = QLabel("Klar")
        self.lbl_header_state.setObjectName("headerState")
        self.lbl_header_state.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header.addWidget(self.lbl_header_state, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addSpacing(6)

        self.btn_header_output = QPushButton("📂  Nedlastingsmappe")
        self.btn_header_output.setObjectName("headerBtn")
        self.btn_header_output.setToolTip("Åpne mappen der nedlastinger lagres")
        self.btn_header_output.clicked.connect(self.open_download_folder)

        self.btn_header_settings = QPushButton("⚙  Innstillinger")
        self.btn_header_settings.setObjectName("headerBtn")
        self.btn_header_settings.setToolTip("Innstillinger (Ctrl+,)")
        self.btn_header_settings.clicked.connect(self.show_settings_dialog)

        self.btn_header_focus = QPushButton("⛶")
        self.btn_header_focus.setObjectName("headerBtn")
        self.btn_header_focus.setFixedWidth(38)
        self.btn_header_focus.setToolTip("Fokusmodus / fullskjerm (F11)")
        self.btn_header_focus.clicked.connect(self.toggle_fullscreen)

        header.addWidget(self.btn_header_output)
        header.addWidget(self.btn_header_settings)
        header.addWidget(self.btn_header_focus)

        main_layout.addWidget(self.app_header)

        self.tabs.setObjectName("pageStack")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().hide()
        self.tabs.setContentsMargins(0, 0, 0, 0)
        self.tabs.currentChanged.connect(self._update_page_header)
        main_layout.addWidget(self.tabs, 1)

        root.addWidget(main, 1)
        self.setCentralWidget(shell)

        status_bar = self.statusBar()
        status_bar.setObjectName("appStatusBar")
        status_bar.setSizeGripEnabled(False)
        status_bar.showMessage("Klar.")
        self.nav_buttons[0].setChecked(True)

    def _set_active_page(self, index):
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def _update_page_header(self, index):
        pages = [
            ("Editor", "Klipp, juster og eksporter mediefilen din"),
            ("Nedlaster", "Lim inn en lenke – eller bygg en kø med mange"),
            ("Info & hjelp", "Detaljer om åpen fil og alle hurtigtaster"),
        ]
        if 0 <= index < len(pages):
            title, subtitle = pages[index]
            if hasattr(self, "page_title"):
                self.page_title.setText(title)
                self.page_subtitle.setText(subtitle)
            for i, btn in enumerate(getattr(self, "nav_buttons", [])):
                btn.setChecked(i == index)


    def _set_app_status(self, message: str):
        """Keep the header status pill and status bar synchronized."""
        if hasattr(self, "lbl_header_state"):
            self.lbl_header_state.setText(message)
        self.statusBar().showMessage(message)

    def show_settings_dialog(self):
        """Compact preferences dialog for the settings that matter most."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Studio Pro – Innstillinger")
        dlg.setMinimumWidth(560)

        layout = QVBoxLayout(dlg)
        title = QLabel("⚙ Innstillinger")
        title.setProperty("heading", True)
        layout.addWidget(title)

        form = QFormLayout()
        out_edit = QLineEdit(self.custom_download_dir or str(DEFAULT_SANGER_DIR))
        out_edit.setPlaceholderText("Standard: prosjektmappen / sanger")
        out_row = QHBoxLayout()
        out_row.addWidget(out_edit, 1)
        browse = QPushButton("📁 Velg")
        browse.setObjectName("secondaryBtn")
        browse.clicked.connect(
            lambda: self._choose_settings_output_folder(out_edit)
        )
        out_row.addWidget(browse)
        form.addRow("Nedlastingsmappe:", out_row)

        auto_editor = QCheckBox("Åpne siste nedlasting automatisk i editor")
        auto_editor.setChecked(
            getattr(self, "chk_auto_editor", None) and self.chk_auto_editor.isChecked()
        )

        clipboard = QCheckBox("Overvåk utklippstavlen etter URL-er")
        clipboard.setChecked(
            getattr(self, "chk_clipboard_watch", None) and self.chk_clipboard_watch.isChecked()
        )

        exported = QCheckBox("Bruk eksporterte YouTube-kapsler automatisk")
        exported.setChecked(
            getattr(self, "chk_auto_exported_cookies", None)
            and self.chk_auto_exported_cookies.isChecked()
        )

        form.addRow("", auto_editor)
        form.addRow("", clipboard)
        form.addRow("", exported)
        layout.addLayout(form)

        hint = QLabel(
            "Tips: Videoeksport beholdes lokalt i «resultat/», mens nedlastinger "
            "går til valgt mappe. Cookie-filer lagres separat og skal holdes private."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94a3b8; padding: 8px 0;")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        reset = QPushButton("↺ Gjenopprett standard")
        reset.setObjectName("secondaryBtn")
        save = QPushButton("💾 Lagre")
        save.setObjectName("accentBtn")
        cancel = QPushButton("Avbryt")
        cancel.setObjectName("secondaryBtn")
        buttons.addWidget(reset)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        def reset_defaults():
            out_edit.setText(str(DEFAULT_SANGER_DIR))
            auto_editor.setChecked(False)
            clipboard.setChecked(False)
            exported.setChecked(True)

        def apply():
            selected = out_edit.text().strip()
            if selected and not os.path.isdir(selected):
                try:
                    os.makedirs(selected, exist_ok=True)
                except OSError as exc:
                    QMessageBox.critical(dlg, "Kunne ikke opprette mappe", str(exc))
                    return

            self.custom_download_dir = selected if selected != str(DEFAULT_SANGER_DIR) else ""
            self.settings.setValue("downloadDir", self.custom_download_dir)
            self.settings.setValue("autoOpenEditor", auto_editor.isChecked())
            self.settings.setValue("clipboardWatch", clipboard.isChecked())
            self.settings.setValue("autoExportedCookies", exported.isChecked())

            if hasattr(self, "chk_auto_editor"):
                self.chk_auto_editor.blockSignals(True)
                self.chk_auto_editor.setChecked(auto_editor.isChecked())
                self.chk_auto_editor.blockSignals(False)
            if hasattr(self, "chk_auto_exported_cookies"):
                self.chk_auto_exported_cookies.blockSignals(True)
                self.chk_auto_exported_cookies.setChecked(exported.isChecked())
                self.chk_auto_exported_cookies.blockSignals(False)
            if hasattr(self, "chk_clipboard_watch"):
                self.chk_clipboard_watch.setChecked(clipboard.isChecked())
            if hasattr(self, "lbl_outdir"):
                self.lbl_outdir.setText(f"Lagres i: {self.get_download_output_dir()}")
            self._save_dl_prefs()
            self._toggle_clipboard_watch()
            self._set_app_status("Innstillinger lagret.")
            dlg.accept()

        reset.clicked.connect(reset_defaults)
        save.clicked.connect(apply)
        cancel.clicked.connect(dlg.reject)
        dlg.exec()

    def _choose_settings_output_folder(self, edit):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Velg nedlastingsmappe",
            edit.text().strip() or str(DEFAULT_SANGER_DIR),
        )
        if folder:
            edit.setText(folder)

    def install_app_shortcuts(self):
        """Application-wide shortcuts that remain useful outside the video viewport."""
        shortcuts = [
            (QKeySequence("Ctrl+O"), self.import_file),
            (QKeySequence("Ctrl+Shift+O"), self.show_recent_files_menu),
            (QKeySequence("Ctrl+B"), self.add_current_time_bookmark),
            (QKeySequence("Ctrl+Shift+S"), self.take_snapshot),
            (QKeySequence("F11"), self.toggle_fullscreen),
            (QKeySequence("Ctrl+1"), lambda: self._set_active_page(0)),
            (QKeySequence("Ctrl+2"), lambda: self._set_active_page(1)),
            (QKeySequence("Ctrl+3"), lambda: self._set_active_page(2)),
            (QKeySequence("Ctrl+,"), self.show_settings_dialog),
            (QKeySequence("I"), lambda: self.video_path and self.capture_start()),
            (QKeySequence("O"), lambda: self.video_path and self.capture_end()),
        ]
        self._app_actions = []
        for sequence, callback in shortcuts:
            action = QAction(self)
            action.setShortcut(sequence)
            action.triggered.connect(callback)
            self.addAction(action)
            self._app_actions.append(action)

    def restore_editor_preferences(self):
        """Restore non-destructive editor preferences saved in QSettings."""
        if not hasattr(self, "combo_speed"):
            return
        self.combo_speed.setCurrentText(self.settings.value("speed", "1.0x"))
        self.combo_res.setCurrentIndex(
            int(self.settings.value("resolution", 0, type=int))
        )
        self.combo_format.setCurrentIndex(
            int(self.settings.value("renderFormat", 0, type=int))
        )
        self.spin_volume_boost.setValue(
            int(self.settings.value("volumeBoost", 100, type=int))
        )
        self.slider_brightness.setValue(
            int(self.settings.value("brightness", 0, type=int))
        )
        self.slider_contrast.setValue(
            int(self.settings.value("contrast", 100, type=int))
        )
        self.slider_saturation.setValue(
            int(self.settings.value("saturation", 100, type=int))
        )
        if hasattr(self, "chk_overwrite_output"):
            self.chk_overwrite_output.setChecked(
                self.settings.value("overwriteOutput", False, type=bool)
            )
        QTimer.singleShot(0, self._restore_editor_splitter)

    def save_editor_preferences(self):
        """Persist the current editor choices so reopening the app feels continuous."""
        if not hasattr(self, "combo_speed"):
            return
        self.settings.setValue("speed", self.combo_speed.currentText())
        self.settings.setValue("resolution", self.combo_res.currentIndex())
        self.settings.setValue("renderFormat", self.combo_format.currentIndex())
        self.settings.setValue("volumeBoost", self.spin_volume_boost.value())
        self.settings.setValue("brightness", self.slider_brightness.value())
        self.settings.setValue("contrast", self.slider_contrast.value())
        self.settings.setValue("saturation", self.slider_saturation.value())
        self.settings.setValue("overwriteOutput", self.chk_overwrite_output.isChecked())
        if hasattr(self, "editor_splitter"):
            self.settings.setValue("editorSplitterV2", self.editor_splitter.sizes())

    def closeEvent(self, event):
        """Stop background workers cleanly before the application exits."""
        self.save_editor_preferences()
        self._save_dl_prefs()
        if hasattr(self, "editor_splitter"):
            self.settings.setValue("editorSplitterV2", self.editor_splitter.sizes())

        for worker_name in (
            "dl_worker",
            "cookie_worker",
            "info_worker",
            "worker",
        ):
            worker = getattr(self, worker_name, None)
            if worker is None or not hasattr(worker, "isRunning") or not worker.isRunning():
                continue
            if hasattr(worker, "cancel"):
                worker.cancel()
            worker.quit()
            if not worker.wait(2500):
                try:
                    worker.terminate()
                    worker.wait(1000)
                except Exception:
                    pass

        self.media_player.stop()
        event.accept()

    def _restore_editor_splitter(self):
        saved = self.settings.value("editorSplitterV2", [])
        if isinstance(saved, list) and len(saved) == 2:
            try:
                self.editor_splitter.setSizes([int(saved[0]), int(saved[1])])
            except (TypeError, ValueError):
                pass

    def check_environment(self):
        ffmpeg_bin = get_ffmpeg_exe()
        spotdl_ok = shutil.which("spotdl") is not None
        try:
            import yt_dlp  # noqa: F401
            ytdlp_ok = True
        except ImportError:
            ytdlp_ok = False

        js_names = list(get_js_runtimes_for_ytdlp().keys()) or ["(ingen – bruker remote ejs)"]

        status_msg = []
        if ffmpeg_is_usable():
            status_msg.append(f"✅ FFmpeg: {Path(ffmpeg_bin).name}")
        else:
            status_msg.append("⚠️ FFmpeg: mangler (pip install imageio-ffmpeg)")

        if ytdlp_ok:
            status_msg.append("✅ yt-dlp: innebygd (Python)")
        else:
            status_msg.append("⚠️ yt-dlp: pip install yt-dlp")

        status_msg.append(f"JS: {', '.join(js_names)}")

        if spotdl_ok:
            status_msg.append("✅ spotdl")
        else:
            status_msg.append("ℹ️ spotdl valgfritt")

        env_summary = " | ".join(status_msg)
        self.lbl_spotify_status.setText(env_summary)
        self.btn_install_deps.setEnabled(not (ytdlp_ok and ffmpeg_is_usable()))
        ready = "✅ Klar" if (ytdlp_ok and ffmpeg_is_usable()) else "⚠ Sjekk"
        self._set_app_status(f"{ready}  •  FFmpeg {'OK' if ffmpeg_is_usable() else 'mangler'}")

    def _inspector_page(self):
        """A scrollable page for the editor inspector tabs. Returns (scroll, layout)."""
        page = QWidget()
        page.setObjectName("inspectorPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll, layout

    def init_editor_tab(self):
        editor_widget = QWidget()
        editor_layout = QHBoxLayout(editor_widget)
        editor_layout.setContentsMargins(16, 16, 16, 16)
        editor_layout.setSpacing(0)

        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.editor_splitter.setHandleWidth(14)
        self.editor_splitter.setChildrenCollapsible(False)
        editor_layout.addWidget(self.editor_splitter)

        # ================= WORKSPACE: file, video, transport, trim =================
        self.right_workspace = QWidget()
        workspace_layout = QVBoxLayout(self.right_workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(10)

        self.lbl_file = QLabel(
            "Ingen fil åpnet  •  dra og slipp en video- eller lydfil hit, eller trykk Ctrl+O"
        )
        self.lbl_file.setObjectName("fileBadge")
        workspace_layout.addWidget(self.lbl_file)

        self.video_container = QFrame()
        self.video_container.setObjectName("videoFrame")
        self.video_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_container.setMinimumHeight(240)
        container_layout = QVBoxLayout(self.video_container)
        container_layout.setContentsMargins(1, 1, 1, 1)

        self.video_widget = QVideoWidget(self.video_container)
        self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        container_layout.addWidget(self.video_widget)
        self.media_player.setVideoOutput(self.video_widget)

        self.click_overlay = ClickOverlay(self.video_widget)
        self.click_overlay.single_click_signal.connect(self.toggle_playback)
        self.click_overlay.double_click_signal.connect(self.toggle_fullscreen)

        workspace_layout.addWidget(self.video_container, stretch=1)

        # ---- Transport bar ----
        self.hud = QFrame()
        self.hud.setObjectName("transportBar")
        hud_layout = QVBoxLayout(self.hud)
        hud_layout.setContentsMargins(14, 10, 14, 10)
        hud_layout.setSpacing(8)

        timeline_row = QHBoxLayout()
        timeline_row.setSpacing(12)
        self.lbl_time_current = QLabel("00:00:00")
        self.lbl_time_current.setObjectName("timecode")
        self.slider_timeline = QSlider(Qt.Orientation.Horizontal)
        self.slider_timeline.setEnabled(False)
        self.slider_timeline.sliderMoved.connect(self.scrub_timeline)
        self.lbl_time_total = QLabel("00:00:00")
        self.lbl_time_total.setObjectName("timecodeMuted")
        timeline_row.addWidget(self.lbl_time_current)
        timeline_row.addWidget(self.slider_timeline, stretch=1)
        timeline_row.addWidget(self.lbl_time_total)
        hud_layout.addLayout(timeline_row)

        media_row = QHBoxLayout()
        media_row.setSpacing(6)

        self.btn_skip_back = QPushButton("⟲ 5s")
        self.btn_skip_back.setObjectName("ghostBtn")
        self.btn_skip_back.setToolTip("Spol 5 sekunder tilbake (Shift+←)")
        self.btn_skip_back.clicked.connect(lambda: self._skip_relative(-5000))

        self.btn_play = QPushButton("▶ Spill av")
        self.btn_play.setObjectName("playBtn")
        self.btn_play.setMinimumWidth(112)
        self.btn_play.setToolTip("Spill av / pause (Mellomrom)")
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self.toggle_playback)

        self.btn_skip_fwd = QPushButton("5s ⟳")
        self.btn_skip_fwd.setObjectName("ghostBtn")
        self.btn_skip_fwd.setToolTip("Spol 5 sekunder fremover (Shift+→)")
        self.btn_skip_fwd.clicked.connect(lambda: self._skip_relative(5000))

        self.combo_speed = QComboBox()
        self.combo_speed.addItems(["0.25x", "0.5x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.combo_speed.setCurrentText("1.0x")
        self.combo_speed.setToolTip("Avspillingshastighet – brukes også når du eksporterer")
        self.combo_speed.currentIndexChanged.connect(self.change_live_speed)

        self.btn_snapshot = QPushButton("📸 Stillbilde")
        self.btn_snapshot.setObjectName("ghostBtn")
        self.btn_snapshot.setToolTip("Lagre gjeldende bilde som PNG (Ctrl+Shift+S)")
        self.btn_snapshot.clicked.connect(self.take_snapshot)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setObjectName("ghostBtn")
        self.btn_mute.setFixedWidth(38)
        self.btn_mute.setToolTip("Lyd av / på")
        self.btn_mute.clicked.connect(self.toggle_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.setToolTip("Avspillingsvolum")
        self.volume_slider.valueChanged.connect(self.adjust_volume)

        self.vol_label = QLabel("🔊 100%")
        self.vol_label.setObjectName("mutedText")
        self.vol_label.setFixedWidth(58)

        self.btn_fullscreen = QPushButton("⛶ Fullskjerm")
        self.btn_fullscreen.setObjectName("ghostBtn")
        self.btn_fullscreen.setToolTip("Forstørr videovisningen (F / F11 / dobbeltklikk)")
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)

        self.btn_menu = QPushButton("•••")
        self.btn_menu.setObjectName("ghostBtn")
        self.btn_menu.setFixedWidth(38)
        self.btn_menu.setToolTip("Flere visningsvalg")
        self.btn_menu.clicked.connect(self.display_three_dots_menu)

        media_row.addWidget(self.btn_skip_back)
        media_row.addWidget(self.btn_play)
        media_row.addWidget(self.btn_skip_fwd)
        media_row.addSpacing(6)
        media_row.addWidget(self._vdivider())
        media_row.addSpacing(6)
        media_row.addWidget(self._field_label("Fart"))
        media_row.addWidget(self.combo_speed)
        media_row.addStretch()
        media_row.addWidget(self.btn_snapshot)
        media_row.addWidget(self._vdivider())
        media_row.addWidget(self.btn_mute)
        media_row.addWidget(self.volume_slider)
        media_row.addWidget(self.vol_label)
        media_row.addWidget(self._vdivider())
        media_row.addWidget(self.btn_fullscreen)
        media_row.addWidget(self.btn_menu)
        hud_layout.addLayout(media_row)

        workspace_layout.addWidget(self.hud)

        # ---- Trim bar (in / out points) ----
        self.trim_panel = QFrame()
        self.trim_panel.setObjectName("card")
        trim_box = QHBoxLayout(self.trim_panel)
        trim_box.setContentsMargins(14, 10, 14, 10)
        trim_box.setSpacing(8)

        trim_title = QLabel("✂  Klipp")
        trim_title.setObjectName("cardTitle")
        trim_box.addWidget(trim_title)
        trim_box.addSpacing(8)

        trim_box.addWidget(self._field_label("Start"))
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setRange(0, 99999)
        self.spin_start.setDecimals(2)
        self.spin_start.setSuffix(" s")
        self.spin_start.setMinimumWidth(96)
        btn_set_start = QPushButton("⇤ Her")
        btn_set_start.setObjectName("secondaryBtn")
        btn_set_start.setToolTip("Sett start til nåværende posisjon (I)")
        btn_set_start.clicked.connect(self.capture_start)
        trim_box.addWidget(self.spin_start)
        trim_box.addWidget(btn_set_start)

        trim_box.addSpacing(10)
        trim_box.addWidget(self._field_label("Slutt"))
        self.spin_end = QDoubleSpinBox()
        self.spin_end.setRange(0, 99999)
        self.spin_end.setDecimals(2)
        self.spin_end.setSuffix(" s")
        self.spin_end.setMinimumWidth(96)
        btn_set_end = QPushButton("Her ⇥")
        btn_set_end.setObjectName("secondaryBtn")
        btn_set_end.setToolTip("Sett slutt til nåværende posisjon (O)")
        btn_set_end.clicked.connect(self.capture_end)
        trim_box.addWidget(self.spin_end)
        trim_box.addWidget(btn_set_end)

        trim_box.addSpacing(10)
        trim_box.addWidget(self._vdivider())
        trim_box.addSpacing(10)
        self.lbl_clip_length = QLabel("Hele filen")
        self.lbl_clip_length.setObjectName("clipLength")
        self.lbl_clip_length.setToolTip("Lengden på klippet som eksporteres")
        trim_box.addWidget(self.lbl_clip_length)
        trim_box.addStretch()

        self.chk_loop_ab = QCheckBox("Spill utvalget i løkke")
        self.chk_loop_ab.setToolTip("Gjenta avspillingen mellom start og slutt")
        self.chk_loop_ab.stateChanged.connect(self.toggle_loop_ab)
        trim_box.addWidget(self.chk_loop_ab)

        self.spin_start.valueChanged.connect(self._update_clip_length)
        self.spin_end.valueChanged.connect(self._update_clip_length)

        workspace_layout.addWidget(self.trim_panel)

        self.editor_splitter.addWidget(self.right_workspace)

        # ================= INSPECTOR: tabs for effects + pinned export =================
        self.inspector_panel = QWidget()
        # Fullscreen/focus mode hides the editor side panel through this legacy name.
        self.scroll_area = self.inspector_panel
        self.inspector_panel.setMinimumWidth(330)
        self.inspector_panel.setMaximumWidth(440)
        inspector_layout = QVBoxLayout(self.inspector_panel)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(12)

        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setObjectName("inspectorTabs")
        self.inspector_tabs.setUsesScrollButtons(False)

        # -- Bilde --
        page, img_box = self._inspector_page()
        img_box.addWidget(self._section_label("Farge"))
        self.slider_brightness = self.create_slider_group(img_box, "Lysstyrke", -100, 100, 0, "")
        self.slider_contrast = self.create_slider_group(img_box, "Kontrast", 50, 200, 100, "%")
        self.slider_saturation = self.create_slider_group(img_box, "Metning", 0, 200, 100, "%")
        img_box.addSpacing(4)
        img_box.addWidget(self._section_label("Orientering"))
        self.combo_flip = QComboBox()
        self.combo_flip.addItems(["Ingen speilvending", "Speilvend vannrett", "Speilvend loddrett"])
        img_box.addWidget(self.combo_flip)
        img_box.addStretch()
        btn_reset = QPushButton("↺  Nullstill alle effekter")
        btn_reset.setObjectName("secondaryBtn")
        btn_reset.setToolTip("Tilbakestiller farge, speilvending, lydprofil og tekst")
        btn_reset.clicked.connect(self.reset_filters)
        img_box.addWidget(btn_reset)
        self.inspector_tabs.addTab(page, "Bilde")

        # -- Lyd --
        page, aud_box = self._inspector_page()
        aud_box.addWidget(self._field_label("Lydprofil"))
        self.combo_eq = QComboBox()
        self.combo_eq.addItems([
            "Normal (ingen effekt)",
            "Bass boost (+8 dB)",
            "Klarere stemmer (diskant)",
            "Nattmodus / normaliser",
        ])
        aud_box.addWidget(self.combo_eq)
        aud_hint = QLabel("Profilen brukes når du eksporterer. Volumet for selve eksporten stiller du inn under «Eksport».")
        aud_hint.setObjectName("mutedText")
        aud_hint.setWordWrap(True)
        aud_box.addWidget(aud_hint)
        aud_box.addStretch()
        self.inspector_tabs.addTab(page, "Lyd")

        # -- Tekst --
        page, txt_box = self._inspector_page()
        txt_box.addWidget(self._field_label("Tekst eller vannmerke"))
        self.txt_watermark = QLineEdit()
        self.txt_watermark.setPlaceholderText("La stå tomt for ingen tekst")
        txt_box.addWidget(self.txt_watermark)

        ov_grid = QHBoxLayout()
        ov_grid.setSpacing(8)
        pos_col = QVBoxLayout()
        pos_col.setSpacing(4)
        pos_col.addWidget(self._field_label("Plassering"))
        self.combo_wm_pos = QComboBox()
        self.combo_wm_pos.addItems(["Nede til høyre", "Nede til venstre", "Oppe til høyre", "Midten"])
        pos_col.addWidget(self.combo_wm_pos)
        size_col = QVBoxLayout()
        size_col.setSpacing(4)
        size_col.addWidget(self._field_label("Størrelse"))
        self.spin_wm_size = QSpinBox()
        self.spin_wm_size.setRange(16, 96)
        self.spin_wm_size.setValue(32)
        self.spin_wm_size.setSuffix(" pt")
        size_col.addWidget(self.spin_wm_size)
        ov_grid.addLayout(pos_col, 2)
        ov_grid.addLayout(size_col, 1)
        txt_box.addLayout(ov_grid)
        txt_box.addStretch()
        self.inspector_tabs.addTab(page, "Tekst")

        # -- Merker --
        page, bm_box = self._inspector_page()
        bm_hint = QLabel("Lagre viktige øyeblikk mens du ser (Ctrl+B). Dobbeltklikk et merke for å hoppe dit.")
        bm_hint.setObjectName("mutedText")
        bm_hint.setWordWrap(True)
        bm_box.addWidget(bm_hint)
        bm_btn_row = QHBoxLayout()
        self.btn_add_bookmark = QPushButton("＋  Nytt merke")
        self.btn_add_bookmark.setObjectName("secondaryBtn")
        self.btn_add_bookmark.clicked.connect(self.add_current_time_bookmark)
        self.btn_delete_bookmark = QPushButton("Slett valgt")
        self.btn_delete_bookmark.setObjectName("dangerBtn")
        self.btn_delete_bookmark.clicked.connect(self.delete_selected_bookmark)
        bm_btn_row.addWidget(self.btn_add_bookmark, 1)
        bm_btn_row.addWidget(self.btn_delete_bookmark)
        bm_box.addLayout(bm_btn_row)
        self.list_bookmarks = QListWidget()
        self.list_bookmarks.setMinimumHeight(140)
        self.list_bookmarks.setToolTip("Dobbeltklikk på et merke for å hoppe dit")
        self.list_bookmarks.itemDoubleClicked.connect(self.jump_to_selected_bookmark)
        bm_box.addWidget(self.list_bookmarks, 1)
        self.inspector_tabs.addTab(page, "Merker")

        inspector_layout.addWidget(self.inspector_tabs, 1)

        # -- Export (always visible) --
        export_card, exp_box = self._card("Eksport", spacing=8)

        exp_box.addWidget(self._field_label("Format"))
        self.combo_format = QComboBox()
        self.combo_format.addItems([
            "Video: MP4 (.mp4)",
            "Video: WebM (.webm)",
            "Video: MKV (.mkv)",
            "Video: MOV (.mov)",
            "Video: GIF-animasjon (.gif)",
            "Lyd: MP3 (.mp3)",
            "Lyd: OGG Vorbis (.ogg)",
            "Lyd: OPUS (.opus)",
            "Lyd: FLAC (.flac)",
            "Lyd: WAV (.wav)",
            "Lyd: AAC (.m4a)",
        ])
        exp_box.addWidget(self.combo_format)

        res_row = QHBoxLayout()
        res_row.setSpacing(8)
        res_col = QVBoxLayout()
        res_col.setSpacing(4)
        res_col.addWidget(self._field_label("Oppløsning"))
        self.combo_res = QComboBox()
        self.combo_res.addItems(["Original", "1080p (Full HD)", "720p (HD)", "480p (SD)", "9:16 vertikal (Shorts)"])
        res_col.addWidget(self.combo_res)
        vol_col = QVBoxLayout()
        vol_col.setSpacing(4)
        vol_col.addWidget(self._field_label("Volum"))
        self.spin_volume_boost = QSpinBox()
        self.spin_volume_boost.setRange(0, 300)
        self.spin_volume_boost.setValue(100)
        self.spin_volume_boost.setSuffix(" %")
        self.spin_volume_boost.setToolTip("Lydstyrke i den eksporterte filen (100 % = uendret)")
        vol_col.addWidget(self.spin_volume_boost)
        res_row.addLayout(res_col, 2)
        res_row.addLayout(vol_col, 1)
        exp_box.addLayout(res_row)

        exp_box.addWidget(self._field_label("Filnavn"))
        self.txt_name = QLineEdit("studio_pro_klipp")
        self.txt_name.setPlaceholderText("Uten filendelse")
        exp_box.addWidget(self.txt_name)

        self.chk_overwrite_output = QCheckBox("Overskriv hvis filen finnes fra før")
        exp_box.addWidget(self.chk_overwrite_output)

        self.btn_render = QPushButton("⚡  Eksporter")
        self.btn_render.setObjectName("accentBtn")
        self.btn_render.setEnabled(False)
        self.btn_render.setMinimumHeight(42)
        self.btn_render.setToolTip("Åpne en fil først for å kunne eksportere")
        self.btn_render.clicked.connect(self.render_file)
        exp_box.addWidget(self.btn_render)

        self.progress_render = QProgressBar()
        self.progress_render.setVisible(False)
        exp_box.addWidget(self.progress_render)

        self.lbl_render_status = QLabel("")
        self.lbl_render_status.setObjectName("statusText")
        self.lbl_render_status.setWordWrap(True)
        exp_box.addWidget(self.lbl_render_status)

        inspector_layout.addWidget(export_card)

        self.editor_splitter.addWidget(self.inspector_panel)
        self.editor_splitter.setStretchFactor(0, 1)
        self.editor_splitter.setStretchFactor(1, 0)

        self.tabs.addTab(editor_widget, "Editor")

    def init_downloader_tab(self):
        dl_widget = QWidget()
        outer = QVBoxLayout(dl_widget)
        outer.setContentsMargins(0, 0, 0, 0)
        page_scroll = QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        dl_layout = QVBoxLayout(content)
        dl_layout.setContentsMargins(24, 20, 24, 24)
        dl_layout.setSpacing(16)
        page_scroll.setWidget(content)
        outer.addWidget(page_scroll)

        # ================= 1. New download =================
        main_card, main_box = self._card(
            "Ny nedlasting",
            "Fungerer med YouTube, TikTok, Vimeo, SoundCloud, Spotify og mange flere. "
            "Spillelister lastes ned i sin helhet.",
        )

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.txt_url = QLineEdit()
        self.txt_url.setObjectName("urlInput")
        self.txt_url.setPlaceholderText("Lim inn en lenke, f.eks. https://www.youtube.com/watch?v=…")
        self.txt_url.setClearButtonEnabled(True)
        self.txt_url.returnPressed.connect(self.execute_web_download)
        btn_paste = QPushButton("📋  Lim inn")
        btn_paste.setObjectName("secondaryBtn")
        btn_paste.setToolTip("Lim inn lenken fra utklippstavlen")
        btn_paste.clicked.connect(self.paste_clipboard_url)
        self.btn_preview_url = QPushButton("🔍  Forhåndsvis")
        self.btn_preview_url.setObjectName("secondaryBtn")
        self.btn_preview_url.setToolTip("Hent tittel og varighet før du laster ned")
        self.btn_preview_url.clicked.connect(self.preview_download_url)
        url_row.addWidget(self.txt_url, 1)
        url_row.addWidget(btn_paste)
        url_row.addWidget(self.btn_preview_url)
        main_box.addLayout(url_row)

        self.lbl_media_preview = QLabel("")
        self.lbl_media_preview.setObjectName("previewBox")
        self.lbl_media_preview.setWordWrap(True)
        self.lbl_media_preview.hide()
        main_box.addWidget(self.lbl_media_preview)

        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)
        fmt_col = QVBoxLayout()
        fmt_col.setSpacing(4)
        fmt_col.addWidget(self._field_label("Format"))
        self.combo_dl_format = QComboBox()
        self.combo_dl_format.addItems([
            "Video: Beste kvalitet (MP4/Beste)",
            "Video: 1080p Full HD",
            "Video: 720p HD",
            "Lyd: MP3 (.mp3)",
            "Lyd: FLAC (.flac)",
            "Lyd: WAV (.wav)",
            "Lyd: AAC (.m4a)",
            "Lyd: OPUS (.opus)",
        ])
        self.combo_dl_format.setMinimumWidth(240)
        fmt_col.addWidget(self.combo_dl_format)

        out_col = QVBoxLayout()
        out_col.setSpacing(4)
        out_col.addWidget(self._field_label("Lagringsmappe"))
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        default_out = self.custom_download_dir or str(DEFAULT_SANGER_DIR)
        self.lbl_outdir = QLabel(f"Lagres i: {default_out}")
        self.lbl_outdir.setObjectName("fileBadge")
        self.lbl_outdir.setMinimumWidth(120)
        self.lbl_outdir.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.btn_select_outdir = QPushButton("Endre…")
        self.btn_select_outdir.setObjectName("secondaryBtn")
        self.btn_select_outdir.clicked.connect(self.choose_download_dir)
        self.btn_open_dl_folder = QPushButton("📂")
        self.btn_open_dl_folder.setObjectName("secondaryBtn")
        self.btn_open_dl_folder.setFixedWidth(40)
        self.btn_open_dl_folder.setToolTip("Åpne mappen")
        self.btn_open_dl_folder.clicked.connect(self.open_download_folder)
        out_row.addWidget(self.lbl_outdir, 1)
        out_row.addWidget(self.btn_select_outdir)
        out_row.addWidget(self.btn_open_dl_folder)
        out_col.addLayout(out_row)

        opts_row.addLayout(fmt_col)
        opts_row.addLayout(out_col, 1)
        main_box.addLayout(opts_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.btn_download = QPushButton("⬇  Last ned")
        self.btn_download.setObjectName("accentBtn")
        self.btn_download.setMinimumHeight(40)
        self.btn_download.setMinimumWidth(160)
        self.btn_download.setToolTip("Start nedlasting (Enter i lenkefeltet)")
        self.btn_download.clicked.connect(self.execute_web_download)
        self.btn_cancel_download = QPushButton("Avbryt")
        self.btn_cancel_download.setObjectName("dangerBtn")
        self.btn_cancel_download.setMinimumHeight(40)
        self.btn_cancel_download.setEnabled(False)
        self.btn_cancel_download.clicked.connect(self.cancel_active_download)
        self.btn_open_in_editor = QPushButton("🎬  Åpne siste i editor")
        self.btn_open_in_editor.setObjectName("secondaryBtn")
        self.btn_open_in_editor.setMinimumHeight(40)
        self.btn_open_in_editor.setEnabled(False)
        self.btn_open_in_editor.clicked.connect(self.open_last_downloaded_in_editor)
        action_row.addWidget(self.btn_download)
        action_row.addWidget(self.btn_cancel_download)
        action_row.addStretch()
        action_row.addWidget(self.btn_open_in_editor)
        main_box.addLayout(action_row)

        self.lbl_dl_status = QLabel("Klar til nedlasting.")
        self.lbl_dl_status.setObjectName("statusText")
        self.lbl_dl_status.setWordWrap(True)
        main_box.addWidget(self.lbl_dl_status)
        self.progress_dl = QProgressBar()
        self.progress_dl.setVisible(False)
        main_box.addWidget(self.progress_dl)

        dl_layout.addWidget(main_card)

        # ================= 2. Queue + options side by side =================
        lower = QHBoxLayout()
        lower.setSpacing(16)

        queue_card, queue_box = self._card(
            "Nedlastingskø",
            "Én lenke per linje. Køen huskes til neste gang. Dobbeltklikk et element for å fjerne det.",
        )
        self.txt_batch_urls = QTextEdit()
        self.txt_batch_urls.setPlaceholderText("https://…\nhttps://…")
        self.txt_batch_urls.setAcceptRichText(False)
        self.txt_batch_urls.setFixedHeight(84)
        queue_box.addWidget(self.txt_batch_urls)

        queue_btn_row = QHBoxLayout()
        queue_btn_row.setSpacing(8)
        self.btn_add_queue = QPushButton("＋  Legg til i køen")
        self.btn_add_queue.setObjectName("secondaryBtn")
        self.btn_add_queue.clicked.connect(self.add_urls_to_queue)
        self.btn_clear_queue = QPushButton("Tøm")
        self.btn_clear_queue.setObjectName("dangerBtn")
        self.btn_clear_queue.clicked.connect(self.clear_download_queue)
        self.btn_start_queue = QPushButton("▶  Kjør køen")
        self.btn_start_queue.setObjectName("accentBtn")
        self.btn_start_queue.clicked.connect(self.start_download_queue)
        queue_btn_row.addWidget(self.btn_add_queue)
        queue_btn_row.addStretch()
        queue_btn_row.addWidget(self.btn_clear_queue)
        queue_btn_row.addWidget(self.btn_start_queue)
        queue_box.addLayout(queue_btn_row)

        self.list_download_queue = QListWidget()
        self.list_download_queue.setMinimumHeight(180)
        self.list_download_queue.setToolTip("Dobbeltklikk for å fjerne et element")
        self.list_download_queue.itemDoubleClicked.connect(self.remove_queue_item)
        queue_box.addWidget(self.list_download_queue, 1)
        lower.addWidget(queue_card, 3)

        opt_card, opt_box = self._card("Alternativer", spacing=6)

        self.chk_auto_editor = QCheckBox("Åpne i editor når nedlastingen er ferdig")
        self.chk_auto_editor.setChecked(self.settings.value("autoOpenEditor", False, type=bool))
        self.chk_auto_editor.stateChanged.connect(self._save_dl_prefs)
        self.chk_clipboard_watch = QCheckBox("Legg kopierte lenker i køen automatisk")
        self.chk_clipboard_watch.setToolTip("Overvåker utklippstavlen etter lenker")
        self.chk_clipboard_watch.setChecked(self.settings.value("clipboardWatch", False, type=bool))
        self.chk_clipboard_watch.stateChanged.connect(self._toggle_clipboard_watch)
        opt_box.addWidget(self._section_label("Arbeidsflyt"))
        opt_box.addWidget(self.chk_auto_editor)
        opt_box.addWidget(self.chk_clipboard_watch)

        self.chk_dl_subs = QCheckBox("Last ned undertekster (.vtt)")
        self.chk_embed_subs = QCheckBox("Bygg undertekstene inn i videoen")
        self.chk_embed_thumb = QCheckBox("Bygg inn miniatyrbilde")
        opt_box.addSpacing(4)
        opt_box.addWidget(self._section_label("Undertekster og omslag"))
        opt_box.addWidget(self.chk_dl_subs)
        opt_box.addWidget(self.chk_embed_subs)
        opt_box.addWidget(self.chk_embed_thumb)

        opt_box.addSpacing(4)
        opt_box.addWidget(self._section_label("YouTube-innlogging"))
        cookie_hint = QLabel(
            "Trengs bare hvis YouTube ber deg logge inn eller bekrefte at du ikke er en robot. "
            "Lukk nettleseren før du eksporterer."
        )
        cookie_hint.setObjectName("mutedText")
        cookie_hint.setWordWrap(True)
        opt_box.addWidget(cookie_hint)

        self.combo_cookies = QComboBox()
        self.combo_cookies.addItems([
            "Ingen innlogging (standard)",
            "Bruk Chrome",
            "Bruk Edge",
            "Bruk Firefox",
            "Bruk Brave",
            "Bruk Opera",
            "📄 Velg cookies.txt-fil …",
        ])
        self.combo_cookies.setToolTip("Hvilken nettleser innloggingen skal hentes fra")
        self.combo_cookies.currentIndexChanged.connect(self.on_cookie_combo_changed)
        opt_box.addWidget(self.combo_cookies)

        self.btn_export_cookies = QPushButton("🍪  Eksporter YouTube-innlogging")
        self.btn_export_cookies.setObjectName("secondaryBtn")
        self.btn_export_cookies.setToolTip(
            "Lagrer innloggingen fra valgt nettleser til cookies/youtube_active.txt"
        )
        self.btn_export_cookies.clicked.connect(self.export_youtube_cookies_one_click)
        opt_box.addWidget(self.btn_export_cookies)

        self.chk_auto_exported_cookies = QCheckBox("Bruk eksportert innlogging automatisk")
        self.chk_auto_exported_cookies.setChecked(
            self.settings.value("autoExportedCookies", True, type=bool)
        )
        self.chk_auto_exported_cookies.stateChanged.connect(self._save_dl_prefs)
        opt_box.addWidget(self.chk_auto_exported_cookies)

        self.lbl_cookie_export = QLabel("")
        self.lbl_cookie_export.setObjectName("mutedText")
        self.lbl_cookie_export.setWordWrap(True)
        opt_box.addWidget(self.lbl_cookie_export)
        active_cookie = COOKIES_DIR / "youtube_active.txt"
        if active_cookie.is_file():
            self.lbl_cookie_export.setText(f"🍪 Aktiv fil: {active_cookie.name}")
            self.lbl_cookie_export.setToolTip(str(active_cookie))
        opt_box.addStretch()
        lower.addWidget(opt_card, 2)

        dl_layout.addLayout(lower)

        # ================= 3. System status =================
        sys_card, sys_box = self._card(spacing=6)
        sys_row = QHBoxLayout()
        sys_row.setSpacing(12)
        sys_title = QLabel("Systemstatus")
        sys_title.setObjectName("cardTitle")
        self.lbl_spotify_status = QLabel("Sjekker avhengigheter …")
        self.lbl_spotify_status.setObjectName("mutedText")
        self.lbl_spotify_status.setWordWrap(True)
        self.btn_install_deps = QPushButton("⬇  Installer det som mangler")
        self.btn_install_deps.setObjectName("secondaryBtn")
        self.btn_install_deps.clicked.connect(self.install_missing_dependencies)
        sys_row.addWidget(sys_title)
        sys_row.addWidget(self.lbl_spotify_status, 1)
        sys_row.addWidget(self.btn_install_deps)
        sys_box.addLayout(sys_row)
        dl_layout.addWidget(sys_card)

        dl_layout.addStretch()
        self.tabs.addTab(dl_widget, "Nedlaster")

        if self.custom_cookie_file and os.path.isfile(self.custom_cookie_file):
            self.combo_cookies.blockSignals(True)
            self.combo_cookies.setCurrentIndex(6)
            self.combo_cookies.blockSignals(False)
            self.lbl_dl_status.setText(f"📄 Cookie-fil: {Path(self.custom_cookie_file).name}")

        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setInterval(1500)
        self._clipboard_timer.timeout.connect(self._poll_clipboard_for_url)
        if self.chk_clipboard_watch.isChecked():
            self._clipboard_timer.start()

        saved_queue = self.settings.value("downloadQueue", [])
        if isinstance(saved_queue, list):
            for u in saved_queue:
                if isinstance(u, str) and u.strip():
                    self._queue_append(u.strip(), persist=False)
            self._refresh_queue_list()

    def init_info_tab(self):
        info_widget = QWidget()
        outer = QVBoxLayout(info_widget)
        outer.setContentsMargins(0, 0, 0, 0)
        page_scroll = QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        info_layout = QHBoxLayout(content)
        info_layout.setContentsMargins(24, 20, 24, 24)
        info_layout.setSpacing(16)
        info_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        page_scroll.setWidget(content)
        outer.addWidget(page_scroll)

        file_card, file_box = self._card("Åpen fil")
        self.lbl_media_meta = QLabel("Ingen fil er åpnet ennå.\n\nÅpne en fil med Ctrl+O eller dra den inn i vinduet.")
        self.lbl_media_meta.setObjectName("metaBox")
        self.lbl_media_meta.setWordWrap(True)
        self.lbl_media_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_media_meta.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        file_box.addWidget(self.lbl_media_meta)
        file_box.addStretch()
        info_layout.addWidget(file_card, 1, Qt.AlignmentFlag.AlignTop)

        sc_card, sc_box = self._card("Hurtigtaster", spacing=4)
        groups = [
            ("Avspilling", [
                ("Mellomrom", "Spill av / pause"),
                ("← / →", "Spol 1 sekund"),
                ("Shift + ← / →", "Spol 5 sekunder"),
                ("F / F11", "Fullskjerm av / på"),
                ("Esc", "Avslutt fullskjerm"),
            ]),
            ("Redigering", [
                ("I", "Sett start på nåværende posisjon"),
                ("O", "Sett slutt på nåværende posisjon"),
                ("Ctrl + B", "Lagre tidsmerke"),
                ("Ctrl + Shift + S", "Lagre stillbilde"),
            ]),
            ("Programmet", [
                ("Ctrl + O", "Åpne fil"),
                ("Ctrl + Shift + O", "Nylige filer"),
                ("Ctrl + 1 / 2 / 3", "Gå til Editor / Nedlaster / Info"),
                ("Ctrl + ,", "Innstillinger"),
            ]),
        ]
        for group_name, items in groups:
            sc_box.addSpacing(6)
            sc_box.addWidget(self._section_label(group_name))
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(6)
            grid.setColumnMinimumWidth(0, 140)
            grid.setColumnStretch(1, 1)
            for row_index, (key, desc) in enumerate(items):
                k_lbl = QLabel(key)
                k_lbl.setObjectName("kbd")
                grid.addWidget(k_lbl, row_index, 0, Qt.AlignmentFlag.AlignLeft)
                grid.addWidget(QLabel(desc), row_index, 1)
            sc_box.addLayout(grid)
        info_layout.addWidget(sc_card, 1, Qt.AlignmentFlag.AlignTop)

        self.tabs.addTab(info_widget, "Info")

    # Helper UI Methods
    def create_slider_group(self, parent_layout, label_text, min_v, max_v, default_v, unit):
        row = QHBoxLayout()
        row.setSpacing(10)
        lbl = QLabel(label_text)
        lbl.setObjectName("fieldLabel")
        lbl.setFixedWidth(70)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(default_v)
        slider.setToolTip("Dobbeltklikk verdien for å nullstille")
        val_txt = QLabel(f"{default_v}{unit}")
        val_txt.setObjectName("valueLabel")
        val_txt.setFixedWidth(42)
        val_txt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_txt.mouseDoubleClickEvent = lambda _e: slider.setValue(default_v)
        slider.valueChanged.connect(lambda v: val_txt.setText(f"{v}{unit}"))
        row.addWidget(lbl)
        row.addWidget(slider, 1)
        row.addWidget(val_txt)
        parent_layout.addLayout(row)
        return slider

    def _skip_relative(self, delta_ms):
        if not self.video_path:
            return
        target = self.media_player.position() + delta_ms
        self.scrub_timeline(max(0, min(self.duration_ms, target)))

    def _update_clip_length(self, *_):
        start = self.spin_start.value()
        end = self.spin_end.value()
        if end <= start:
            self.lbl_clip_length.setText("Hele filen")
            return
        length = end - start
        minutes, seconds = divmod(length, 60)
        self.lbl_clip_length.setText(f"Lengde {int(minutes):02d}:{seconds:05.2f}")

    def format_timestamp(self, ms):
        total_sec = max(0, int(ms // 1000))
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    # File Drag & Drop
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = str(url.toLocalFile())
            if self.is_supported_file(path):
                self.load_target_asset(path)
                break

    def is_supported_file(self, path):
        return Path(path).suffix.lower() in MEDIA_EXTENSIONS

    def import_file(self):
        file_types = "Alle mediefiler (*.mp4 *.webm *.mkv *.mov *.avi *.m4v *.gif *.mp3 *.wav *.ogg *.opus *.flac *.m4a *.aac);;Video (*.mp4 *.webm *.mkv *.mov *.avi *.m4v *.gif);;Lyd (*.mp3 *.wav *.ogg *.opus *.flac *.m4a *.aac)"
        path, _ = QFileDialog.getOpenFileName(self, "Åpne mediefil", "", file_types)
        if path:
            self.load_target_asset(path)

    def add_to_recent_files(self, path):
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[:10]
        self.settings.setValue("recentFiles", self.recent_files)

    def show_recent_files_menu(self):
        menu = QMenu(self)
        if not self.recent_files:
            menu.addAction("(ingen nylige filer)").setEnabled(False)
        else:
            for f in self.recent_files:
                if os.path.exists(f):
                    action = QAction(Path(f).name, self)
                    action.setToolTip(f)
                    action.triggered.connect(lambda checked, p=f: self.load_target_asset(p))
                    menu.addAction(action)
            menu.addSeparator()
            clear_action = QAction("🗑 Tøm historikk", self)
            clear_action.triggered.connect(self.clear_recent_files)
            menu.addAction(clear_action)
        menu.exec(QCursor.pos())

    def clear_recent_files(self):
        self.recent_files = []
        self.settings.setValue("recentFiles", [])

    def choose_download_dir(self):
        start = self.custom_download_dir or str(Path(__file__).parent / "sanger")
        folder = QFileDialog.getExistingDirectory(self, "Velg mappen for nedlastinger", start)
        if folder:
            self.custom_download_dir = folder
            self.settings.setValue("downloadDir", folder)
            self.lbl_outdir.setText(f"Lagres i: {folder}")

    def get_download_output_dir(self):
        out_dir = self.custom_download_dir or str(DEFAULT_SANGER_DIR)
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _prefer_exported_cookies(self):
        return self.chk_auto_exported_cookies.isChecked()

    def _save_dl_prefs(self):
        self.settings.setValue("autoExportedCookies", self.chk_auto_exported_cookies.isChecked())
        self.settings.setValue("autoOpenEditor", self.chk_auto_editor.isChecked())

    def _toggle_clipboard_watch(self):
        enabled = self.chk_clipboard_watch.isChecked()
        self.settings.setValue("clipboardWatch", enabled)
        if enabled:
            self._clipboard_timer.start()
        else:
            self._clipboard_timer.stop()

    def paste_clipboard_url(self):
        text = QApplication.clipboard().text().strip()
        if text:
            self.txt_url.setText(text)

    def _poll_clipboard_for_url(self):
        text = QApplication.clipboard().text().strip()
        if not text or text == self._clipboard_last:
            return
        if text.startswith("http") or text.startswith("spotify:"):
            self._clipboard_last = text
            self.txt_url.setText(text)
            self._queue_append(text, persist=True)
            self.lbl_dl_status.setText(f"📎 URL fra utklippstavle lagt i kø ({len(self.download_queue)} ventende)")

    def _queue_append(self, url, persist=True):
        for item in self.download_queue:
            if item["url"] == url and item["status"] in ("pending", "active"):
                return
        qid = len(self.download_queue) + 1
        self.download_queue.append({
            "id": qid,
            "url": url,
            "status": "pending",
            "filepath": "",
            "error": "",
        })
        if persist:
            self._persist_queue()
        self._refresh_queue_list()

    def _persist_queue(self):
        pending = [i["url"] for i in self.download_queue if i["status"] in ("pending", "active")]
        self.settings.setValue("downloadQueue", pending)

    def _refresh_queue_list(self):
        self.list_download_queue.clear()
        icons = {"pending": "⏳", "active": "▶", "done": "✅", "failed": "❌"}
        for item in self.download_queue:
            ic = icons.get(item["status"], "•")
            short = item["url"] if len(item["url"]) < 72 else item["url"][:69] + "…"
            text = f"{ic} {short}"
            self.list_download_queue.addItem(text)

    def add_urls_to_queue(self):
        urls = parse_url_lines(self.txt_url.text())
        urls.extend(parse_url_lines(self.txt_batch_urls.toPlainText()))
        if not urls:
            QMessageBox.information(self, "Ingen URL-er", "Lim inn minst én gyldig http(s)- eller Spotify-lenke.")
            return
        for u in urls:
            self._queue_append(u)
        self.txt_batch_urls.clear()
        self.lbl_dl_status.setText(f"📋 {len(urls)} lenker lagt i kø (totalt {len(self.download_queue)}).")

    def remove_queue_item(self, item):
        row = self.list_download_queue.row(item)
        if 0 <= row < len(self.download_queue):
            if self.download_queue[row]["status"] == "active":
                return
            del self.download_queue[row]
            self._persist_queue()
            self._refresh_queue_list()

    def clear_download_queue(self):
        if self.dl_worker and self.dl_worker.isRunning():
            QMessageBox.warning(self, "Kø aktiv", "Avbryt pågående nedlasting først.")
            return
        self.download_queue = [i for i in self.download_queue if i["status"] == "active"]
        self._persist_queue()
        self._refresh_queue_list()

    def start_download_queue(self):
        pending = [i for i in self.download_queue if i["status"] == "pending"]
        if not pending:
            self.add_urls_to_queue()
        self.pump_download_queue()

    def pump_download_queue(self):
        if self.dl_worker and self.dl_worker.isRunning():
            return
        next_item = next((i for i in self.download_queue if i["status"] == "pending"), None)
        if not next_item:
            self._queue_active = False
            self.btn_download.setEnabled(True)
            self.btn_cancel_download.setEnabled(False)
            done = sum(1 for i in self.download_queue if i["status"] == "done")
            failed = sum(1 for i in self.download_queue if i["status"] == "failed")
            if done or failed:
                self.lbl_dl_status.setText(f"📋 Kø ferdig: {done} OK, {failed} feilet.")
                self.download_queue = [i for i in self.download_queue if i["status"] in ("pending", "active")]
                self._persist_queue()
                self._refresh_queue_list()
            return
        self._queue_active = True
        self._current_queue_id = next_item["id"]
        next_item["status"] = "active"
        self._refresh_queue_list()
        self._run_download_url(next_item["url"])

    def export_youtube_cookies_one_click(self):
        idx = self.combo_cookies.currentIndex()
        if idx in BROWSER_COOKIE_MAP:
            browser = BROWSER_COOKIE_MAP[idx]
        else:
            browser = "edge"
        reply = QMessageBox.question(
            self,
            "Eksporter YouTube-kapsler",
            f"Dette leser kapsler fra **{browser}** og lagrer Netscape-fil i:\n{COOKIES_DIR}\n\n"
            "Viktig: Lukk nettleseren helt først, og vær innlogget på youtube.com.\n\nFortsette?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.btn_export_cookies.setEnabled(False)
        self.lbl_dl_status.setText(f"🍪 Eksporterer kapsler fra {browser} …")
        self.cookie_worker = CookieExportWorker(browser)
        self.cookie_worker.status.connect(self.lbl_dl_status.setText)
        self.cookie_worker.finished.connect(self.on_cookie_export_finished)
        self.cookie_worker.start()

    def on_cookie_export_finished(self, success, message):
        self.btn_export_cookies.setEnabled(True)
        if success:
            active = COOKIES_DIR / "youtube_active.txt"
            self.custom_cookie_file = str(active)
            self.settings.setValue("cookieFile", str(active))
            self.combo_cookies.blockSignals(True)
            self.combo_cookies.setCurrentIndex(6)
            self.combo_cookies.blockSignals(False)
            self.lbl_cookie_export.setText(f"🍪 {message.replace(chr(10), ' | ')}")
            self.lbl_dl_status.setText("✅ YouTube-kapsler klare – prøv nedlasting igjen!")
            QMessageBox.information(self, "Kapsler eksportert", message)
        else:
            self.lbl_dl_status.setText("❌ Kunne ikke eksportere kapsler.")
            self.show_detailed_error_dialog("Kapsel-eksport feilet", message)

    def open_download_folder(self):
        path = self.get_download_output_dir()
        if os.name == "nt":
            os.startfile(path)
        else:
            webbrowser.open(f"file://{path}")

    def install_missing_dependencies(self):
        self.btn_install_deps.setEnabled(False)
        self.lbl_dl_status.setText("⏳ Installerer yt-dlp + EJS-støtte, imageio-ffmpeg og spotdl …")
        QApplication.processEvents()
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "imageio-ffmpeg", "spotdl"],
                check=False,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.lbl_dl_status.setText("✅ Avhengigheter oppdatert – prøv nedlasting igjen.")
        except Exception as e:
            self.lbl_dl_status.setText(f"❌ Installasjon feilet: {e}")
        self.check_environment()

    def preview_download_url(self):
        url = self.txt_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Mangler lenke", "Lim inn en URL først.")
            return
        if "spotify.com" in url or "spotify:" in url:
            self.lbl_media_preview.setText("🎧 Spotify-lenke – bruk «Start Nedlasting» (krever spotdl).")
            self.lbl_media_preview.show()
            return
        self.btn_preview_url.setEnabled(False)
        self.lbl_media_preview.setText("⏳ Henter info …")
        self.lbl_media_preview.show()
        cookie_idx = self.combo_cookies.currentIndex()
        self.info_worker = MediaInfoWorker(url, cookie_idx, getattr(self, "custom_cookie_file", None))
        self.info_worker.finished.connect(self.on_preview_finished)
        self.info_worker.start()

    def on_preview_finished(self, success, message):
        self.btn_preview_url.setEnabled(True)
        if success:
            self.lbl_media_preview.setText(message)
        else:
            self.lbl_media_preview.setText(f"⚠️ {message}")

    def cancel_active_download(self):
        if self.dl_worker and self.dl_worker.isRunning():
            if hasattr(self.dl_worker, "cancel"):
                self.dl_worker.cancel()
            self.lbl_dl_status.setText("Avbryter …")

    def find_newest_media_in_dir(self, out_dir, since_ts=0.0):
        candidates = []
        for name in os.listdir(out_dir):
            path = os.path.join(out_dir, name)
            if not os.path.isfile(path):
                continue
            if Path(path).suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            mtime = os.path.getmtime(path)
            if since_ts and mtime < since_ts - 2:
                continue
            candidates.append(path)
        if not candidates:
            return ""
        return max(candidates, key=os.path.getmtime)

    def load_target_asset(self, path):
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Fil ikke funnet", f"Fila finnes ikke:\n{path}")
            return
        self.video_path = path
        self.add_to_recent_files(path)

        # Inspect using OpenCV
        file_size_mb = os.path.getsize(path) / (1024 * 1024)
        cap = cv2.VideoCapture(path)
        if cap.isOpened():
            self.video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.fps = cap.get(cv2.CAP_PROP_FPS)
            if self.fps <= 0:
                self.fps = 30.0
        else:
            self.video_width, self.video_height, self.fps = 1920, 1080, 30.0
        cap.release()

        base = Path(path).stem
        self.txt_name.setText(f"{base}_redigert")
        self.lbl_file.setText(f"🎞️ {Path(path).name} ({self.video_width}x{self.video_height} | {self.fps:.1f} FPS | {file_size_mb:.1f} MB)")

        # Update metadata tab info
        meta_info = f"""📌 Filnavn: {Path(path).name}
📁 Sti: {path}
📐 Oppløsning: {self.video_width} x {self.video_height} px
⚡ Framerate (FPS): {self.fps:.2f}
📦 Filstørrelse: {file_size_mb:.2f} MB
⏱ Formatsjekk: OK
"""
        self.lbl_media_meta.setText(meta_info)

        # Set media source
        self.media_player.setSource(QUrl.fromLocalFile(path))
        self._set_app_status(f"🎬 Åpnet: {Path(path).name}")

        self.btn_play.setEnabled(True)
        self.btn_render.setEnabled(True)
        self.list_bookmarks.clear()
        self.bookmarks_data.clear()

        # Switch tab to Editor
        self.tabs.setCurrentIndex(0)

        # Play media
        self.media_player.play()
        self.btn_play.setText("⏸ Pause")

    def toggle_playback(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.btn_play.setText("▶ Spill av")
        else:
            self.change_live_speed()
            self.media_player.play()
            self.btn_play.setText("⏸ Pause")

    def change_live_speed(self):
        try:
            rate = float(self.combo_speed.currentText().replace("x", ""))
        except (TypeError, ValueError):
            rate = 1.0
        self.media_player.setPlaybackRate(max(0.05, rate))
        self._set_app_status(f"▶ Avspillingshastighet: {rate:g}x")

    def scrub_timeline(self, position):
        self.media_player.setPosition(position)

    def on_player_position_changed(self, position):
        self.slider_timeline.blockSignals(True)
        self.slider_timeline.setValue(position)
        self.slider_timeline.blockSignals(False)
        self.lbl_time_current.setText(self.format_timestamp(position))

        # Check A-B Loop
        if self.is_loop_ab:
            start_ms = int(self.spin_start.value() * 1000)
            end_ms = int(self.spin_end.value() * 1000)
            if end_ms > start_ms:
                if position >= end_ms or position < start_ms:
                    self.media_player.setPosition(start_ms)

    def on_player_duration_changed(self, duration):
        self.duration_ms = duration
        self.slider_timeline.setRange(0, duration)
        self.slider_timeline.setEnabled(True)
        self.lbl_time_total.setText(self.format_timestamp(duration))
        self.spin_start.setRange(0.0, duration / 1000.0)
        self.spin_end.setRange(0.0, duration / 1000.0)
        self.spin_end.setValue(duration / 1000.0)

    def adjust_volume(self, value):
        self.audio_output.setVolume(value / 100.0)
        self.vol_label.setText(f"🔊 {value}%")
        self.btn_mute.setText("🔇" if value == 0 else "🔊")

    def toggle_mute(self):
        self.muted = not self.muted
        self.audio_output.setMuted(self.muted)
        self.btn_mute.setText("🔇" if self.muted else "🔊")

    def toggle_loop_ab(self, state):
        self.is_loop_ab = state == Qt.CheckState.Checked.value

    def toggle_fullscreen(self):
        """Distraction-free media focus mode."""
        if not self.is_fullscreen_mode:
            self.normal_geometry = self.geometry()
            if hasattr(self, "nav_panel"):
                self.nav_panel.hide()
            if hasattr(self, "app_header"):
                self.app_header.hide()
            self.scroll_area.hide()
            self.trim_panel.hide()
            self.lbl_file.hide()
            self.showFullScreen()
            self.is_fullscreen_mode = True
            self.btn_fullscreen.setText("⛶ Vindu")
        else:
            self.showNormal()
            if hasattr(self, "nav_panel"):
                self.nav_panel.show()
            if hasattr(self, "app_header"):
                self.app_header.show()
            self.scroll_area.show()
            self.trim_panel.show()
            self.lbl_file.show()
            if self.normal_geometry:
                self.setGeometry(self.normal_geometry)
            self.is_fullscreen_mode = False
            self.btn_fullscreen.setText("⛶ Fullskjerm")

        QApplication.processEvents()
        self.resize_overlay()

    def display_three_dots_menu(self):
        menu = QMenu(self)
        
        aspect_menu = menu.addMenu("📐 Bildeforhold")
        act_keep = QAction("Behold størrelsesforhold (Keep Aspect)", self)
        act_keep.triggered.connect(lambda: self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio))
        act_fill = QAction("Fyll ut hele ruten (Ignore Aspect)", self)
        act_fill.triggered.connect(lambda: self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.IgnoreAspectRatio))
        aspect_menu.addAction(act_keep)
        aspect_menu.addAction(act_fill)

        menu.addSeparator()
        fs_act = QAction("🖥️ Bytt fullskjerm", self)
        fs_act.triggered.connect(self.toggle_fullscreen)
        menu.addAction(fs_act)

        menu.exec(QCursor.pos())

    def take_snapshot(self):
        """Grabs the current video frame using OpenCV and saves to file."""
        if not self.video_path:
            QMessageBox.warning(self, "Ingen fil", "Åpne en videofil først!")
            return

        pos_ms = self.media_player.position()
        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, pos_ms)
        ret, frame = cap.read()
        cap.release()

        if ret and frame is not None:
            project_dir = Path(__file__).parent
            snapshots_dir = project_dir / "bildekutt"
            os.makedirs(snapshots_dir, exist_ok=True)
            
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_file = str(snapshots_dir / f"bildekutt_{timestamp_str}.png")
            cv2.imwrite(out_file, frame)

            QMessageBox.information(
                self, "Bildekutt lagret 🎉",
                f"Gjeldende bilde ble lagret i høy kvalitet:\n\n📷 {out_file}"
            )
        else:
            QMessageBox.warning(self, "Feil", "Kunne ikke fange bilde fra videoststrømmen.")

    def add_current_time_bookmark(self):
        if not self.video_path:
            return
        pos = self.media_player.position()
        time_str = self.format_timestamp(pos)
        text = f"🔖 Øyeblikk: {time_str}"
        if text not in self.bookmarks_data:
            self.bookmarks_data[text] = pos
            self.list_bookmarks.addItem(text)

    def delete_selected_bookmark(self):
        item = self.list_bookmarks.currentItem()
        if item:
            text = item.text()
            if text in self.bookmarks_data:
                del self.bookmarks_data[text]
            self.list_bookmarks.takeItem(self.list_bookmarks.row(item))

    def jump_to_selected_bookmark(self, item):
        text = item.text()
        if text in self.bookmarks_data:
            self.media_player.setPosition(self.bookmarks_data[text])

    def capture_start(self):
        self.spin_start.setValue(self.media_player.position() / 1000.0)

    def capture_end(self):
        self.spin_end.setValue(self.media_player.position() / 1000.0)

    def reset_filters(self):
        self.slider_brightness.setValue(0)
        self.slider_contrast.setValue(100)
        self.slider_saturation.setValue(100)
        self.combo_flip.setCurrentIndex(0)
        self.combo_eq.setCurrentIndex(0)
        self.txt_watermark.clear()

    def render_file(self):
        if not self.video_path:
            return
        self.media_player.pause()

        start = self.spin_start.value()
        end = self.spin_end.value()
        out_name = safe_output_stem(self.txt_name.text())
        format_idx = self.combo_format.currentIndex()

        ext_map = [".mp4", ".webm", ".mkv", ".mov", ".gif",
                   ".mp3", ".ogg", ".opus", ".flac", ".wav", ".m4a"]
        out_ext = ext_map[format_idx]

        project_dir = Path(__file__).parent
        out_dir = project_dir / "resultat"
        os.makedirs(out_dir, exist_ok=True)

        overwrite = self.chk_overwrite_output.isChecked()
        candidate = out_dir / f"{out_name}{out_ext}"
        if candidate.exists() and not overwrite:
            n = 2
            while True:
                candidate = out_dir / f"{out_name}_{n}{out_ext}"
                if not candidate.exists():
                    break
                n += 1
        output_path = str(candidate)

        ffmpeg_bin = get_ffmpeg_exe()
        if not ffmpeg_bin:
            QMessageBox.critical(
                self, "FFmpeg mangler",
                f"FFmpeg ble ikke funnet.\n\nInstaller med:\n{sys.executable} -m pip install imageio-ffmpeg",
            )
            return
        cmd = [ffmpeg_bin, "-y" if overwrite else "-n", "-i", self.video_path]

        total_dur = (end - start) if end > start else (self.duration_ms / 1000.0 - start)
        total_dur = max(0.1, total_dur)

        if start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        if end > start:
            cmd += ["-t", f"{end - start:.3f}"]

        # Build Video & Audio Filters
        video_filters = []
        audio_filters = []

        # Color filters
        b_ff = self.slider_brightness.value() / 100.0
        c_ff = self.slider_contrast.value() / 100.0
        s_ff = self.slider_saturation.value() / 100.0
        if b_ff != 0.0 or c_ff != 1.0 or s_ff != 1.0:
            video_filters.append(f"eq=brightness={b_ff}:contrast={c_ff}:saturation={s_ff}")

        # Flips
        flip_idx = self.combo_flip.currentIndex()
        if flip_idx == 1:
            video_filters.append("hflip")
        elif flip_idx == 2:
            video_filters.append("vflip")

        # Watermark Text Overlay
        wm_text = self.txt_watermark.text().strip()
        if wm_text:
            pos_idx = self.combo_wm_pos.currentIndex()
            font_size = self.spin_wm_size.value()
            escaped_text = escape_drawtext(wm_text)
            pos_coords = "x=w-tw-20:y=h-th-20"  # bottom right
            if pos_idx == 1:
                pos_coords = "x=20:y=h-th-20"    # bottom left
            elif pos_idx == 2:
                pos_coords = "x=w-tw-20:y=20"     # top right
            elif pos_idx == 3:
                pos_coords = "x=(w-tw)/2:y=(h-th)/2" # center

            video_filters.append(f"drawtext=text='{escaped_text}':fontsize={font_size}:fontcolor=white:{pos_coords}")

        # Equalizer & Sound Effects
        eq_idx = self.combo_eq.currentIndex()
        if eq_idx == 1:
            audio_filters.append("bass=g=8")
        elif eq_idx == 2:
            audio_filters.append("treble=g=6")
        elif eq_idx == 3:
            audio_filters.append("loudnorm")

        # Speed filter
        speed = float(self.combo_speed.currentText().replace('x', ''))
        if speed != 1.0:
            video_filters.append(f"setpts={1.0/speed}*PTS")
            audio_filters.extend(build_atempo_chain(speed))

        # Audio Volume Boost
        vol_boost = self.spin_volume_boost.value()
        if vol_boost != 100:
            vol_val = vol_boost / 100.0
            audio_filters.append(f"volume={vol_val:.2f}")

        # Resolution preset
        res_idx = self.combo_res.currentIndex()
        if res_idx == 1:
            video_filters.append("scale=1920:1080")
        elif res_idx == 2:
            video_filters.append("scale=1280:720")
        elif res_idx == 3:
            video_filters.append("scale=854:480")
        elif res_idx == 4:
            video_filters.append("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2")

        # Apply Format & Codec Configurations
        if format_idx == 4:  # GIF Export
            if video_filters:
                vf_chain = ",".join(video_filters) + ",split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
            else:
                vf_chain = "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
            cmd += ["-vf", vf_chain, "-r", "15", "-an"]
        elif format_idx < 5:  # Standard Video Export
            if video_filters:
                cmd += ["-vf", ",".join(video_filters)]
            if audio_filters:
                cmd += ["-af", ",".join(audio_filters)]

            if format_idx == 1:  # WebM
                cmd += ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus"]
            else:  # MP4 / MKV / MOV
                cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k"]
                if format_idx in (0, 3):
                    cmd += ["-movflags", "+faststart"]
        else:  # Audio Only Export
            if audio_filters:
                cmd += ["-af", ",".join(audio_filters)]
            audio_codecs = {
                5: "libmp3lame", 6: "libvorbis", 7: "libopus",
                8: "flac", 9: "pcm_s16le", 10: "aac"
            }
            codec = audio_codecs[format_idx]
            cmd += ["-vn", "-c:a", codec]
            if format_idx == 5:
                cmd += ["-q:a", "0"]
            elif format_idx == 10:
                cmd += ["-b:a", "256k"]

        cmd.append(output_path)

        self.progress_render.setVisible(True)
        self.progress_render.setValue(0)
        self.btn_render.setEnabled(False)
        self.lbl_render_status.setText("⚡ Starter rendering ...")

        self.worker = RenderWorker(cmd, output_path, total_duration_s=total_dur)
        self.worker.progress.connect(self.progress_render.setValue)
        self.worker.status.connect(self.lbl_render_status.setText)
        self.worker.finished.connect(self.on_render_finished)
        self.worker.start()

    def on_render_finished(self, success, message):
        self.progress_render.setVisible(False)
        self.btn_render.setEnabled(True)
        if success:
            self.lbl_render_status.setText("✅ Rendering fullført!")
            QMessageBox.information(self, "Suksess! 🎉", f"Videoen din er klar!\n\n📁 {message}")
        else:
            self.lbl_render_status.setText("❌ Rendering feilet.")
            self.show_detailed_error_dialog("Feil under rendering", message)

    def execute_web_download(self):
        if self.dl_worker and self.dl_worker.isRunning():
            QMessageBox.information(self, "Nedlasting pågår", "Vent til aktiv nedlasting er ferdig, eller trykk Avbryt.")
            return
        if self.txt_url.text().strip():
            self._queue_append(self.txt_url.text().strip())
        self.start_download_queue()

    def _run_download_url(self, url):
        if not url:
            self.pump_download_queue()
            return

        if not getattr(self, "_in_download_retry", False):
            self._yt_cookie_retry_done = False
        self._in_download_retry = False

        fmt_idx = self.combo_dl_format.currentIndex()
        cookie_idx = self.combo_cookies.currentIndex()
        prefer = self._prefer_exported_cookies()

        self.btn_download.setEnabled(False)
        self.btn_cancel_download.setEnabled(True)
        self.progress_dl.setVisible(True)
        self.progress_dl.setValue(0)
        self._download_started_at = time.time()
        self._active_download_url = url

        out_dir = self.get_download_output_dir()
        cookie_file = getattr(self, "custom_cookie_file", None) or None

        if "spotify.com" in url or "spotify:" in url:
            audio_exts = ["mp3", "mp3", "mp3", "mp3", "flac", "wav", "m4a", "opus"]
            spotify_fmt = audio_exts[min(fmt_idx, len(audio_exts) - 1)]
            self.dl_worker = SpotifyDownloadWorker(
                url, out_dir, spotify_fmt, cookie_idx, cookie_file, prefer,
            )
        else:
            self.dl_worker = YtDlpDownloadWorker(
                url, out_dir, fmt_idx, cookie_idx, cookie_file, prefer,
                write_subs=self.chk_dl_subs.isChecked(),
                embed_subs=self.chk_embed_subs.isChecked(),
                embed_thumbnail=self.chk_embed_thumb.isChecked(),
            )
        self.dl_worker.status.connect(self.lbl_dl_status.setText)
        self.dl_worker.progress.connect(self.progress_dl.setValue)
        self.dl_worker.finished.connect(self.on_download_finished)
        self.dl_worker.start()

    def on_cookie_combo_changed(self, idx):
        if idx == 6:
            path, _ = QFileDialog.getOpenFileName(self, "Velg cookie-fil (.txt)", "", "Tekstfiler (*.txt);;Alle filer (*.*)")
            if path:
                self.custom_cookie_file = path
                self.settings.setValue("cookieFile", path)
                self.lbl_dl_status.setText(f"📄 Cookie-fil valgt: {Path(path).name}")
            else:
                self.combo_cookies.setCurrentIndex(0)

    def _update_active_queue_item(self, success, message, filepath=""):
        if not self._current_queue_id:
            return
        for item in self.download_queue:
            if item["id"] == self._current_queue_id:
                item["status"] = "done" if success else "failed"
                item["error"] = "" if success else message[:500]
                item["filepath"] = filepath
                break
        self._refresh_queue_list()
        self._persist_queue()

    def on_download_finished(self, success, message):
        self.progress_dl.setVisible(False)
        queue_mode = self._queue_active or any(i["status"] == "active" for i in self.download_queue)

        if success:
            self.lbl_dl_status.setText("✅ Nedlasting fullført!")
            out_dir = self.get_download_output_dir()
            if message and os.path.isfile(message):
                self.last_downloaded_file = message
            else:
                newest = self.find_newest_media_in_dir(out_dir, self._download_started_at)
                if newest:
                    self.last_downloaded_file = newest

            if self.last_downloaded_file:
                self.btn_open_in_editor.setEnabled(True)
                if self.chk_auto_editor.isChecked():
                    self.load_target_asset(self.last_downloaded_file)

            self._update_active_queue_item(True, message, self.last_downloaded_file or "")

            if not queue_mode:
                display_path = self.last_downloaded_file or out_dir
                QMessageBox.information(
                    self, "Nedlasting Ferdig 🎧",
                    f"Fila ligger her:\n{display_path}",
                )
            self._current_queue_id = None
            QTimer.singleShot(400, self.pump_download_queue)
            return

        msg_lower = message.lower()
        active = COOKIES_DIR / "youtube_active.txt"
        if ("not a bot" in msg_lower or "sign in to confirm" in msg_lower) and not active.is_file():
            if not getattr(self, "_yt_cookie_retry_done", False):
                self._yt_cookie_retry_done = True
                self.lbl_dl_status.setText("🤖 YouTube krever kapsler – prøver 1-klikk eksport (Edge) …")
                self._in_download_retry = True
                self.combo_cookies.setCurrentIndex(2)
                self.export_youtube_cookies_one_click()
                return

        if "cookie database" in msg_lower or "failed to decrypt" in msg_lower or "dpapi" in msg_lower:
            if self.combo_cookies.currentIndex() in BROWSER_COOKIE_MAP and not getattr(self, "_cookie_decrypt_retried", False):
                self._cookie_decrypt_retried = True
                self.combo_cookies.setCurrentIndex(0)
                self.lbl_dl_status.setText("⚠️ Nettleser låst – prøver eksporterte/lagrede kapsler …")
                self._in_download_retry = True
                QTimer.singleShot(500, lambda: self._run_download_url(getattr(self, "_active_download_url", "")))
                return
        self._cookie_decrypt_retried = False

        if ("not a bot" in msg_lower or "sign in to confirm" in msg_lower):
            if not getattr(self, "_yt_cookie_retry_done", False):
                self._yt_cookie_retry_done = True
                if active.is_file():
                    self.combo_cookies.blockSignals(True)
                    self.combo_cookies.setCurrentIndex(6)
                    self.custom_cookie_file = str(active)
                    self.combo_cookies.blockSignals(False)
                    self.lbl_dl_status.setText("🍪 Prøver lagrede YouTube-kapsler …")
                    self._in_download_retry = True
                    QTimer.singleShot(500, lambda: self._run_download_url(getattr(self, "_active_download_url", "")))
                    return
                for try_idx in (2, 1, 3):
                    self.combo_cookies.setCurrentIndex(try_idx)
                    self.lbl_dl_status.setText("🤖 Prøver nettleser-kapsler – lukk nettleseren …")
                    self._in_download_retry = True
                    QTimer.singleShot(800, lambda: self._run_download_url(getattr(self, "_active_download_url", "")))
                    return

        self._update_active_queue_item(False, message)
        self._current_queue_id = None
        self.lbl_dl_status.setText("❌ Nedlasting feilet.")
        if queue_mode:
            QTimer.singleShot(600, self.pump_download_queue)
        else:
            self.btn_download.setEnabled(True)
            self.btn_cancel_download.setEnabled(False)
        self.show_detailed_error_dialog("Nedlastingsfeil", message)

    def open_last_downloaded_in_editor(self):
        if self.last_downloaded_file and os.path.exists(self.last_downloaded_file):
            self.load_target_asset(self.last_downloaded_file)

    def show_detailed_error_dialog(self, title, message):
        """Displays a clean scrollable error dialog with diagnostic output."""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(600, 420)

        layout = QVBoxLayout(dlg)
        lbl = QLabel(f"<b>{title}:</b>")
        layout.addWidget(lbl)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(message)
        layout.addWidget(txt)

        if "not a bot" in message.lower() or "sign in" in message.lower():
            bot_hint = QLabel(
                "🤖 <b>YouTube Bot-sjekk oppdaget på denne videoen:</b><br>"
                "YouTube krever verifisering for denne spesifikke URL-en.<br><br>"
                "<b>Løsning (Velg én):</b><br>"
                "1. <b>Lukk Chrome eller Edge helt</b> (slik at Windows låser opp kapsel-databasen), og velg <i>'Kapsler: Chrome'</i> eller <i>'Edge'</i>.<br>"
                "2. <b>Velg en cookie-fil:</b> Velg <i>'Kapsler: Velg cookie-fil (.txt)'</i> fra menyen og velg en eksportert `cookies.txt` fil fra din nettleser!"
            )
            bot_hint.setWordWrap(True)
            bot_hint.setStyleSheet("color: #f43f5e; background: rgba(244, 63, 94, 0.1); border: 1px solid #f43f5e; border-radius: 8px; padding: 12px; font-size: 13px;")
            layout.addWidget(bot_hint)
        else:
            hint = QLabel("💡 <b>Tips:</b> Hvis videoen krever innlogging eller er aldersbegrenset, velg nettleseren din under <i>'Kapsler/Cookies'</i> i menyen (husk å lukke nettleseren først).")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #38bdf8; padding: 6px;")
            layout.addWidget(hint)

        btn_ok = QPushButton("Forstått / Lukk")
        btn_ok.clicked.connect(dlg.accept)
        layout.addWidget(btn_ok)

        dlg.exec()

    def keyPressEvent(self, event):
        if event.key() in [Qt.Key.Key_F, Qt.Key.Key_F11]:
            self.toggle_fullscreen()
            return
        elif event.key() == Qt.Key.Key_Escape and self.is_fullscreen_mode:
            self.toggle_fullscreen()
            return

        if not self.video_path:
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_Space:
            self.toggle_playback()
        elif event.key() == Qt.Key.Key_Left:
            step = 5000 if event.modifiers() & Qt.KeyboardModifier.Shift else 1000
            self.scrub_timeline(max(0, self.media_player.position() - step))
        elif event.key() == Qt.Key.Key_Right:
            step = 5000 if event.modifiers() & Qt.KeyboardModifier.Shift else 1000
            self.scrub_timeline(min(self.duration_ms, self.media_player.position() + step))
        elif event.matches(QKeySequence.StandardKey.Open):
            self.import_file()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_overlay()

    def resize_overlay(self):
        if hasattr(self, 'click_overlay') and hasattr(self, 'video_widget'):
            self.click_overlay.setGeometry(0, 0, self.video_widget.width(), self.video_widget.height())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    editor = StudioProMasterSuite()
    editor.show()
    sys.exit(app.exec())
