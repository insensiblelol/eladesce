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
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
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
    """Pick JS runtimes yt-dlp needs for YouTube (2026+)."""
    runtimes = {}
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            runtimes[name] = {}
    return runtimes


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
        "extractor_args": {
            "youtube": {"player_client": ["tv", "android", "ios", "web", "mweb"]},
        },
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
        self.setCentralWidget(self.tabs)

        self.init_editor_tab()
        self.init_downloader_tab()
        self.init_info_tab()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        QTimer.singleShot(100, self.check_environment)

        if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            QTimer.singleShot(200, lambda: self.load_target_asset(sys.argv[1]))

    def setup_stylesheet(self):
        self.setStyleSheet("""
            /* Main Window & Core Styling */
            QMainWindow, QWidget {
                background-color: #08090d;
                color: #e2e8f0;
                font-family: "Segoe UI", "Inter", -apple-system, sans-serif;
            }
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0f172a, stop:0.5 #08090d, stop:1 #1e1b4b);
            }

            /* Tab Navigation */
            QTabWidget::pane {
                border: 1px solid rgba(255, 255, 255, 0.08);
                background: rgba(15, 23, 42, 0.65);
                border-radius: 14px;
            }
            QTabBar::tab {
                background: rgba(30, 41, 59, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.08);
                padding: 12px 26px;
                font-weight: 600;
                font-size: 13px;
                color: #94a3b8;
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                margin-right: 4px;
            }
            QTabBar::tab:hover {
                background: rgba(51, 65, 85, 0.8);
                color: #f1f5f9;
            }
            QTabBar::tab:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:1 #a855f7);
                color: #ffffff;
                border: none;
            }

            /* Buttons */
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:1 #8b5cf6);
                color: #ffffff;
                border: none;
                padding: 10px 18px;
                border-radius: 10px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4f46e5, stop:1 #7c3aed);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4338ca, stop:1 #6d28d9);
            }
            QPushButton:disabled {
                background: #1e293b;
                color: #64748b;
            }
            QPushButton#secondaryBtn {
                background: #1e293b;
                border: 1px solid #334155;
                color: #cbd5e1;
            }
            QPushButton#secondaryBtn:hover {
                background: #334155;
                color: #ffffff;
            }
            QPushButton#accentBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #10b981, stop:1 #06b6d4);
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton#accentBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #059669, stop:1 #0891b2);
            }

            /* Inputs & Controls */
            QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit, QListWidget, QTextEdit {
                background: rgba(15, 23, 42, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.12);
                color: #f8fafc;
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 13px;
                selection-background-color: #6366f1;
            }
            QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #818cf8;
            }
            QComboBox QAbstractItemView {
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 8px;
                selection-background-color: #6366f1;
                color: #f8fafc;
            }

            /* Lists */
            QListWidget::item {
                padding: 8px 12px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 6px;
            }
            QListWidget::item:hover {
                background: rgba(99, 102, 241, 0.2);
            }
            QListWidget::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:1 #8b5cf6);
                color: #ffffff;
            }

            /* Sliders */
            QSlider::groove:horizontal {
                height: 6px;
                background: #1e293b;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #818cf8, stop:1 #c084fc);
                width: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:1 #a855f7);
                border-radius: 3px;
            }

            /* Progress Bar */
            QProgressBar {
                background: #1e293b;
                border: none;
                border-radius: 8px;
                height: 20px;
                text-align: center;
                color: #ffffff;
                font-weight: 700;
                font-size: 12px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #10b981, stop:1 #3b82f6);
                border-radius: 8px;
            }

            /* Labels & Groupboxes */
            QLabel {
                color: #cbd5e1;
                font-size: 13px;
            }
            QLabel[heading="true"] {
                font-size: 20px;
                font-weight: 800;
                color: #f8fafc;
            }
            QGroupBox {
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                margin-top: 14px;
                padding-top: 10px;
                background: rgba(15, 23, 42, 0.4);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
                color: #818cf8;
                font-weight: 700;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #0f172a;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #334155;
                border-radius: 4px;
                min-height: 25px;
            }
            QScrollBar::handle:vertical:hover {
                background: #475569;
            }
        """)

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

        self.lbl_spotify_status.setText(" | ".join(status_msg))
        self.btn_install_deps.setEnabled(not (ytdlp_ok and ffmpeg_is_usable()))

    def init_editor_tab(self):
        editor_widget = QWidget()
        editor_layout = QHBoxLayout(editor_widget)
        editor_layout.setContentsMargins(16, 16, 16, 16)
        editor_layout.setSpacing(20)

        # Splitter to allow user resizing between Sidebar and Video Viewport
        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        editor_layout.addWidget(self.editor_splitter)

        # ---------- LEFT PANEL (Sidebar Controls inside ScrollArea) ----------
        self.controls_panel = QWidget()
        self.controls_panel.setObjectName("controlsPanel")
        controls_layout = QVBoxLayout(self.controls_panel)
        controls_layout.setContentsMargins(14, 14, 14, 14)
        controls_layout.setSpacing(14)

        # File Import Section
        top_row = QHBoxLayout()
        btn_import = QPushButton("📁 Åpne fil")
        btn_import.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        btn_import.clicked.connect(self.import_file)
        
        self.btn_recent = QPushButton("🗂 Nylig")
        self.btn_recent.setObjectName("secondaryBtn")
        self.btn_recent.clicked.connect(self.show_recent_files_menu)

        top_row.addWidget(btn_import, stretch=2)
        top_row.addWidget(self.btn_recent, stretch=1)
        controls_layout.addLayout(top_row)

        self.lbl_file = QLabel("Ingen mediefil åpnet – dra & slipp en fil her!")
        self.lbl_file.setWordWrap(True)
        self.lbl_file.setStyleSheet("color: #94a3b8; font-style: italic; background: rgba(0,0,0,0.2); padding: 10px; border-radius: 8px;")
        controls_layout.addWidget(self.lbl_file)

        # Bookmarks Group Box
        grp_bookmarks = QGroupBox("📌 Øyeblikk & Tidsmerker")
        bm_box = QVBoxLayout(grp_bookmarks)
        
        bm_btn_row = QHBoxLayout()
        self.btn_add_bookmark = QPushButton("➕ Lagre merke")
        self.btn_add_bookmark.setStyleSheet("background: #ec4899; color: white;")
        self.btn_add_bookmark.clicked.connect(self.add_current_time_bookmark)

        self.btn_delete_bookmark = QPushButton("🗑 Slett")
        self.btn_delete_bookmark.setObjectName("secondaryBtn")
        self.btn_delete_bookmark.clicked.connect(self.delete_selected_bookmark)
        
        bm_btn_row.addWidget(self.btn_add_bookmark)
        bm_btn_row.addWidget(self.btn_delete_bookmark)
        bm_box.addLayout(bm_btn_row)

        self.list_bookmarks = QListWidget()
        self.list_bookmarks.setMaximumHeight(110)
        self.list_bookmarks.setToolTip("Dobbeltklikk på et merke for å hoppe rett dit!")
        self.list_bookmarks.itemDoubleClicked.connect(self.jump_to_selected_bookmark)
        bm_box.addWidget(self.list_bookmarks)
        controls_layout.addWidget(grp_bookmarks)

        # Filters Group Box
        grp_filters = QGroupBox("🎨 Farge og Bildejusteringer")
        flt_box = QVBoxLayout(grp_filters)
        self.slider_brightness = self.create_slider_group(flt_box, "Lysstyrke:", -100, 100, 0, "")
        self.slider_contrast   = self.create_slider_group(flt_box, "Kontrast:",    50, 200, 100, "%")
        self.slider_saturation = self.create_slider_group(flt_box, "Metning:",      0, 200, 100, "%")

        extra_tools = QHBoxLayout()
        self.combo_flip = QComboBox()
        self.combo_flip.addItems(["Ingen rotering", "Speilvend vannrett", "Speilvend loddrett"])
        
        btn_reset = QPushButton("🔄 Nullstill")
        btn_reset.setObjectName("secondaryBtn")
        btn_reset.clicked.connect(self.reset_filters)
        
        extra_tools.addWidget(self.combo_flip)
        extra_tools.addWidget(btn_reset)
        flt_box.addLayout(extra_tools)
        controls_layout.addWidget(grp_filters)

        # Equalizer & Audio FX Group Box
        grp_audio_fx = QGroupBox("🎛️ Lyd-Equalizer & Effekter")
        afx_box = QVBoxLayout(grp_audio_fx)
        
        self.combo_eq = QComboBox()
        self.combo_eq.addItems([
            "Normal (Ingen effekt)",
            "Bass Boost (+8dB)",
            "Vokalforsterker (Treble Boost)",
            "Nattmodus / Normaliser (Loudnorm)",
        ])
        afx_box.addWidget(QLabel("Lydprofil:"))
        afx_box.addWidget(self.combo_eq)
        controls_layout.addWidget(grp_audio_fx)

        # Text & Watermark Overlay Group Box
        grp_overlay = QGroupBox("🏷️ Tekst / Vannmerke")
        ov_box = QVBoxLayout(grp_overlay)
        self.txt_watermark = QLineEdit()
        self.txt_watermark.setPlaceholderText("Skriv inn tekst for overlagring ...")
        ov_box.addWidget(self.txt_watermark)

        ov_row = QHBoxLayout()
        self.combo_wm_pos = QComboBox()
        self.combo_wm_pos.addItems(["Nede i høyre hjørne", "Nede i venstre hjørne", "Oppe i høyre hjørne", "Midten"])
        self.spin_wm_size = QSpinBox()
        self.spin_wm_size.setRange(16, 96)
        self.spin_wm_size.setValue(32)
        self.spin_wm_size.setSuffix(" pt")
        ov_row.addWidget(self.combo_wm_pos)
        ov_row.addWidget(self.spin_wm_size)
        ov_box.addLayout(ov_row)
        controls_layout.addWidget(grp_overlay)

        # Export & Render Settings Group Box
        grp_export = QGroupBox("⚙️ Eksportinnstillinger")
        exp_box = QVBoxLayout(grp_export)

        # Playback & Export Speed
        speed_layout = QHBoxLayout()
        self.combo_speed = QComboBox()
        self.combo_speed.addItems(["0.25x", "0.5x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.combo_speed.setCurrentText("1.0x")
        self.combo_speed.currentIndexChanged.connect(self.change_live_speed)
        speed_layout.addWidget(QLabel("Hastighet:"))
        speed_layout.addWidget(self.combo_speed)
        exp_box.addLayout(speed_layout)

        # Target Resolution
        res_layout = QHBoxLayout()
        self.combo_res = QComboBox()
        self.combo_res.addItems(["Original oppløsning", "1080p (Full HD)", "720p (HD)", "480p (SD)", "9:16 Vertikal (Shorts)"])
        res_layout.addWidget(QLabel("Oppløsning:"))
        res_layout.addWidget(self.combo_res)
        exp_box.addLayout(res_layout)

        # Audio Gain / Volume Boost
        vol_boost_row = QHBoxLayout()
        vol_boost_row.addWidget(QLabel("Lydstyrke eksport:"))
        self.spin_volume_boost = QSpinBox()
        self.spin_volume_boost.setRange(0, 300)
        self.spin_volume_boost.setValue(100)
        self.spin_volume_boost.setSuffix("%")
        vol_boost_row.addWidget(self.spin_volume_boost)
        exp_box.addLayout(vol_boost_row)

        # Format selector
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

        self.txt_name = QLineEdit("studio_pro_klipp")
        exp_box.addWidget(self.txt_name)

        self.btn_render = QPushButton("⚡ Start Rendering!")
        self.btn_render.setObjectName("accentBtn")
        self.btn_render.setEnabled(False)
        self.btn_render.setStyleSheet("padding: 14px; font-size: 15px;")
        self.btn_render.clicked.connect(self.render_file)
        exp_box.addWidget(self.btn_render)

        self.progress_render = QProgressBar()
        self.progress_render.setVisible(False)
        exp_box.addWidget(self.progress_render)

        self.lbl_render_status = QLabel("")
        self.lbl_render_status.setStyleSheet("color: #38bdf8; font-size: 12px;")
        exp_box.addWidget(self.lbl_render_status)

        controls_layout.addWidget(grp_export)
        controls_layout.addStretch()

        # Wrap controls panel into QScrollArea
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(self.controls_panel)
        self.scroll_area.setMinimumWidth(360)

        self.editor_splitter.addWidget(self.scroll_area)

        # ---------- RIGHT WORKSPACE (Video Player & Controls) ----------
        self.right_workspace = QWidget()
        workspace_layout = QVBoxLayout(self.right_workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(10)

        # Video Viewport Container
        self.video_container = QWidget()
        self.video_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_container.setStyleSheet("""
            background-color: #000000;
            border-radius: 16px;
            border: 1px solid rgba(255,255,255,0.08);
        """)
        container_layout = QVBoxLayout(self.video_container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget(self.video_container)
        self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        container_layout.addWidget(self.video_widget)
        self.media_player.setVideoOutput(self.video_widget)

        self.click_overlay = ClickOverlay(self.video_widget)
        self.click_overlay.single_click_signal.connect(self.toggle_playback)
        self.click_overlay.double_click_signal.connect(self.toggle_fullscreen)

        workspace_layout.addWidget(self.video_container, stretch=5)

        # HUD (Playback Controls & Timeline)
        self.hud = QWidget()
        self.hud.setStyleSheet("""
            QWidget {
                background: rgba(15, 23, 42, 0.75);
                border-radius: 14px;
                border: 1px solid rgba(255,255,255,0.06);
            }
        """)
        hud_layout = QVBoxLayout(self.hud)
        hud_layout.setContentsMargins(16, 12, 16, 12)

        # Timeline Slider Row
        timeline_row = QHBoxLayout()
        self.lbl_time_current = QLabel("00:00:00")
        self.lbl_time_current.setStyleSheet("font-weight: 700; color: #818cf8;")

        self.slider_timeline = QSlider(Qt.Orientation.Horizontal)
        self.slider_timeline.setEnabled(False)
        self.slider_timeline.sliderMoved.connect(self.scrub_timeline)

        self.lbl_time_total = QLabel("00:00:00")
        self.lbl_time_total.setStyleSheet("font-weight: 700; color: #94a3b8;")

        timeline_row.addWidget(self.lbl_time_current)
        timeline_row.addWidget(self.slider_timeline, stretch=1)
        timeline_row.addWidget(self.lbl_time_total)
        hud_layout.addLayout(timeline_row)

        # Media Action Row
        media_row = QHBoxLayout()

        self.btn_play = QPushButton("▶ Spill av")
        self.btn_play.setFixedWidth(110)
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self.toggle_playback)

        self.btn_snapshot = QPushButton("📸 Bildekutt")
        self.btn_snapshot.setObjectName("secondaryBtn")
        self.btn_snapshot.setToolTip("Lagre gjeldende ramme som et bilde (PNG)")
        self.btn_snapshot.clicked.connect(self.take_snapshot)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.valueChanged.connect(self.adjust_volume)

        self.vol_label = QLabel("🔊 100%")
        self.vol_label.setFixedWidth(60)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setObjectName("secondaryBtn")
        self.btn_mute.setFixedWidth(42)
        self.btn_mute.clicked.connect(self.toggle_mute)

        self.btn_fullscreen = QPushButton("🖥️ Fullskjerm")
        self.btn_fullscreen.setObjectName("secondaryBtn")
        self.btn_fullscreen.setToolTip("Forstørr videovisningen (F / F11 / Dobbelklikk)")
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)

        self.btn_menu = QPushButton("•••")
        self.btn_menu.setObjectName("secondaryBtn")
        self.btn_menu.setFixedWidth(42)
        self.btn_menu.clicked.connect(self.display_three_dots_menu)

        media_row.addWidget(self.btn_play)
        media_row.addWidget(self.btn_snapshot)
        media_row.addSpacing(10)
        media_row.addWidget(self.vol_label)
        media_row.addWidget(self.volume_slider)
        media_row.addWidget(self.btn_mute)
        media_row.addStretch()
        media_row.addWidget(self.btn_fullscreen)
        media_row.addWidget(self.btn_menu)
        hud_layout.addLayout(media_row)

        workspace_layout.addWidget(self.hud)

        # Trimming Panel (A-B Range Cut)
        self.trim_panel = QGroupBox("✂️ Klipping & Utvikling (Start - Slutt)")
        trim_box = QHBoxLayout(self.trim_panel)
        trim_box.setContentsMargins(14, 8, 14, 8)

        # Start Spin
        trim_box.addWidget(QLabel("Start (s):"))
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setRange(0, 99999)
        self.spin_start.setSuffix(" s")
        btn_set_start = QPushButton("📌 Sett Start")
        btn_set_start.setObjectName("secondaryBtn")
        btn_set_start.clicked.connect(self.capture_start)
        trim_box.addWidget(self.spin_start)
        trim_box.addWidget(btn_set_start)

        trim_box.addSpacing(20)

        # End Spin
        trim_box.addWidget(QLabel("Slutt (s):"))
        self.spin_end = QDoubleSpinBox()
        self.spin_end.setRange(0, 99999)
        self.spin_end.setSuffix(" s")
        btn_set_end = QPushButton("📌 Sett Slutt")
        btn_set_end.setObjectName("secondaryBtn")
        btn_set_end.clicked.connect(self.capture_end)
        trim_box.addWidget(self.spin_end)
        trim_box.addWidget(btn_set_end)

        self.chk_loop_ab = QCheckBox("🔁 Løkke A-B")
        self.chk_loop_ab.stateChanged.connect(self.toggle_loop_ab)
        trim_box.addWidget(self.chk_loop_ab)

        workspace_layout.addWidget(self.trim_panel)

        self.editor_splitter.addWidget(self.right_workspace)
        self.editor_splitter.setStretchFactor(0, 1)
        self.editor_splitter.setStretchFactor(1, 3)

        self.tabs.addTab(editor_widget, "🎬 Klipp & Rediger")

    def init_downloader_tab(self):
        dl_widget = QWidget()
        dl_layout = QVBoxLayout(dl_widget)
        dl_layout.setContentsMargins(36, 36, 36, 36)
        dl_layout.setSpacing(20)

        title = QLabel("🌐 Media Nedlaster (YouTube, Spotify, SoundCloud, TikTok, Vimeo)")
        title.setProperty("heading", True)
        dl_layout.addWidget(title)

        subtitle = QLabel(
            "Lim inn en URL (YouTube, TikTok, Vimeo, SoundCloud, m.fl.). "
            "Spillelister lastes ned automatisk. For YouTube: lukk Chrome/Edge og velg kapsler hvis nedlasting feiler."
        )
        subtitle.setStyleSheet("color: #94a3b8;")
        subtitle.setWordWrap(True)
        dl_layout.addWidget(subtitle)

        url_row = QHBoxLayout()
        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("https://www.youtube.com/watch?v=... eller Spotify/TikTok-lenke")
        self.txt_url.setMinimumHeight(44)
        
        btn_paste = QPushButton("📋 Lim inn")
        btn_paste.setObjectName("secondaryBtn")
        btn_paste.clicked.connect(lambda: self.txt_url.setText(QApplication.clipboard().text()))
        
        btn_paste_clip = QPushButton("📎 Fra utklipp")
        btn_paste_clip.setObjectName("secondaryBtn")
        btn_paste_clip.setToolTip("Lim inn URL fra utklippstavlen i feltet")
        btn_paste_clip.clicked.connect(self.paste_clipboard_url)

        url_row.addWidget(self.txt_url, stretch=4)
        url_row.addWidget(btn_paste, stretch=1)
        url_row.addWidget(btn_paste_clip)
        dl_layout.addLayout(url_row)

        queue_group = QGroupBox("📋 Nedlastingskø")
        queue_box = QVBoxLayout(queue_group)
        self.txt_batch_urls = QTextEdit()
        self.txt_batch_urls.setPlaceholderText(
            "Lim inn flere URL-er her (én per linje) – spillelister, Spotify-album, TikTok, osv."
        )
        self.txt_batch_urls.setMaximumHeight(88)
        queue_box.addWidget(self.txt_batch_urls)

        queue_btn_row = QHBoxLayout()
        self.btn_add_queue = QPushButton("➕ Legg i kø")
        self.btn_add_queue.setObjectName("secondaryBtn")
        self.btn_add_queue.clicked.connect(self.add_urls_to_queue)
        self.btn_clear_queue = QPushButton("🗑 Tøm kø")
        self.btn_clear_queue.setObjectName("secondaryBtn")
        self.btn_clear_queue.clicked.connect(self.clear_download_queue)
        self.btn_start_queue = QPushButton("▶ Kjør kø")
        self.btn_start_queue.setObjectName("accentBtn")
        self.btn_start_queue.clicked.connect(self.start_download_queue)
        queue_btn_row.addWidget(self.btn_add_queue)
        queue_btn_row.addWidget(self.btn_start_queue)
        queue_btn_row.addWidget(self.btn_clear_queue)
        queue_box.addLayout(queue_btn_row)

        self.list_download_queue = QListWidget()
        self.list_download_queue.setMaximumHeight(140)
        self.list_download_queue.setToolTip("Dobbeltklikk for å fjerne et element")
        self.list_download_queue.itemDoubleClicked.connect(self.remove_queue_item)
        queue_box.addWidget(self.list_download_queue)
        dl_layout.addWidget(queue_group)

        options_row = QHBoxLayout()
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
        
        self.combo_cookies = QComboBox()
        self.combo_cookies.addItems([
            "Kapsler/Cookies: Ingen (Standard)",
            "Kapsler: Chrome (Lukket nettleser)",
            "Kapsler: Edge (Lukket nettleser)",
            "Kapsler: Firefox",
            "Kapsler: Brave",
            "Kapsler: Opera",
            "Kapsler: 📄 Velg cookie-fil (.txt) ...",
        ])
        self.combo_cookies.setToolTip("Bruk nettleserkapsler dersom YouTube krever at du er innlogget eller bekrefter at du ikke er en bot.")
        self.combo_cookies.currentIndexChanged.connect(self.on_cookie_combo_changed)

        self.btn_export_cookies = QPushButton("🍪 Eksporter YouTube-kapsler")
        self.btn_export_cookies.setObjectName("secondaryBtn")
        self.btn_export_cookies.setToolTip(
            "Eksporter innloggede YouTube-kapsler til cookies/youtube_active.txt (lukk nettleseren først)"
        )
        self.btn_export_cookies.clicked.connect(self.export_youtube_cookies_one_click)

        self.chk_auto_exported_cookies = QCheckBox("Bruk eksporterte kapsler automatisk")
        self.chk_auto_exported_cookies.setChecked(
            self.settings.value("autoExportedCookies", True, type=bool)
        )
        self.chk_auto_exported_cookies.stateChanged.connect(self._save_dl_prefs)

        self.chk_clipboard_watch = QCheckBox("Overvåk utklippstavle for URL")
        self.chk_clipboard_watch.setChecked(
            self.settings.value("clipboardWatch", False, type=bool)
        )
        self.chk_clipboard_watch.stateChanged.connect(self._toggle_clipboard_watch)

        self.chk_dl_subs = QCheckBox("Undertekster (.vtt)")
        self.chk_embed_subs = QCheckBox("Embed undertekster i video")
        self.chk_embed_thumb = QCheckBox("Embed thumbnail")
        self.chk_auto_editor = QCheckBox("Åpne i editor etter nedlasting")
        self.chk_auto_editor.setChecked(self.settings.value("autoOpenEditor", False, type=bool))
        self.chk_auto_editor.stateChanged.connect(self._save_dl_prefs)

        self.btn_select_outdir = QPushButton("📁 Velg mappe")
        self.btn_select_outdir.setObjectName("secondaryBtn")
        self.btn_select_outdir.clicked.connect(self.choose_download_dir)

        options_row.addWidget(QLabel("Format:"))
        options_row.addWidget(self.combo_dl_format)
        options_row.addWidget(self.combo_cookies)
        options_row.addWidget(self.btn_select_outdir)
        options_row.addWidget(self.btn_export_cookies)
        dl_layout.addLayout(options_row)

        opts_row2 = QHBoxLayout()
        opts_row2.addWidget(self.chk_auto_exported_cookies)
        opts_row2.addWidget(self.chk_clipboard_watch)
        opts_row2.addWidget(self.chk_dl_subs)
        opts_row2.addWidget(self.chk_embed_subs)
        opts_row2.addWidget(self.chk_embed_thumb)
        opts_row2.addWidget(self.chk_auto_editor)
        dl_layout.addLayout(opts_row2)

        self.lbl_cookie_export = QLabel("")
        self.lbl_cookie_export.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self.lbl_cookie_export.setWordWrap(True)
        dl_layout.addWidget(self.lbl_cookie_export)
        active_cookie = COOKIES_DIR / "youtube_active.txt"
        if active_cookie.is_file():
            self.lbl_cookie_export.setText(f"🍪 Aktiv kapsel-fil: {active_cookie}")

        default_out = self.custom_download_dir or str(DEFAULT_SANGER_DIR)
        self.lbl_outdir = QLabel(f"Lagres i: {default_out}")
        self.lbl_outdir.setStyleSheet("color: #818cf8; font-size: 12px;")
        dl_layout.addWidget(self.lbl_outdir)

        self.lbl_media_preview = QLabel("")
        self.lbl_media_preview.setWordWrap(True)
        self.lbl_media_preview.setStyleSheet(
            "color: #cbd5e1; background: rgba(15,23,42,0.6); padding: 10px; border-radius: 8px; font-size: 13px;"
        )
        self.lbl_media_preview.hide()
        dl_layout.addWidget(self.lbl_media_preview)

        btn_row = QHBoxLayout()
        self.btn_download = QPushButton("📥 Start Nedlasting")
        self.btn_download.setObjectName("accentBtn")
        self.btn_download.setStyleSheet("padding: 14px; font-size: 15px;")
        self.btn_download.clicked.connect(self.execute_web_download)
        
        self.btn_open_in_editor = QPushButton("🎬 Åpne i Redigering")
        self.btn_open_in_editor.setObjectName("secondaryBtn")
        self.btn_open_in_editor.setStyleSheet("padding: 14px; font-size: 15px;")
        self.btn_open_in_editor.setEnabled(False)
        self.btn_open_in_editor.clicked.connect(self.open_last_downloaded_in_editor)

        self.btn_preview_url = QPushButton("🔍 Forhåndsvis")
        self.btn_preview_url.setObjectName("secondaryBtn")
        self.btn_preview_url.clicked.connect(self.preview_download_url)

        self.btn_open_dl_folder = QPushButton("📂 Åpne mappe")
        self.btn_open_dl_folder.setObjectName("secondaryBtn")
        self.btn_open_dl_folder.clicked.connect(self.open_download_folder)

        self.btn_cancel_download = QPushButton("⏹ Avbryt")
        self.btn_cancel_download.setObjectName("secondaryBtn")
        self.btn_cancel_download.setEnabled(False)
        self.btn_cancel_download.clicked.connect(self.cancel_active_download)

        self.btn_install_deps = QPushButton("⬇ Installer avhengigheter")
        self.btn_install_deps.setObjectName("secondaryBtn")
        self.btn_install_deps.clicked.connect(self.install_missing_dependencies)

        btn_row.addWidget(self.btn_download, stretch=3)
        btn_row.addWidget(self.btn_open_in_editor, stretch=2)
        dl_layout.addLayout(btn_row)

        btn_row2 = QHBoxLayout()
        btn_row2.addWidget(self.btn_preview_url)
        btn_row2.addWidget(self.btn_open_dl_folder)
        btn_row2.addWidget(self.btn_cancel_download)
        btn_row2.addWidget(self.btn_install_deps)
        dl_layout.addLayout(btn_row2)

        self.lbl_dl_status = QLabel("Klar til nedlasting.")
        self.lbl_dl_status.setStyleSheet("color: #cbd5e1; font-style: italic;")
        dl_layout.addWidget(self.lbl_dl_status)

        self.progress_dl = QProgressBar()
        self.progress_dl.setVisible(False)
        dl_layout.addWidget(self.progress_dl)

        self.lbl_spotify_status = QLabel("Status: Sjekker systemavhengigheter ...")
        self.lbl_spotify_status.setStyleSheet("color: #94a3b8; font-size: 12px;")
        dl_layout.addWidget(self.lbl_spotify_status)

        dl_layout.addStretch()
        self.tabs.addTab(dl_widget, "🌐 Nedlaster")

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
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(36, 36, 36, 36)
        info_layout.setSpacing(20)

        title = QLabel("ℹ️ Fil-informasjon & Snarveier")
        title.setProperty("heading", True)
        info_layout.addWidget(title)

        self.lbl_media_meta = QLabel("Ingen aktiv fil lastet inn.")
        self.lbl_media_meta.setWordWrap(True)
        self.lbl_media_meta.setStyleSheet("""
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(255,255,255,0.08);
            padding: 18px;
            border-radius: 12px;
            font-family: monospace;
            font-size: 13px;
        """)
        info_layout.addWidget(self.lbl_media_meta)

        shortcuts_group = QGroupBox("⌨️ Tastatursnarveier")
        sc_box = QVBoxLayout(shortcuts_group)
        shortcuts = [
            ("Mellomrom (Space)", "Spill av / Pause video"),
            ("Venstre piltast (←)", "Spol 1 sekund tilbake"),
            ("Høyre piltast (→)", "Spol 1 sekund fremover"),
            ("F / F11 / Dobbelklikk", "Bytt fullskjermvisning"),
            ("Escape (Esc)", "Avslutt fullskjermvisning"),
            ("Ctrl + O", "Åpne ny mediefil"),
        ]
        for key, desc in shortcuts:
            row = QHBoxLayout()
            k_lbl = QLabel(f"<b>{key}</b>")
            k_lbl.setFixedWidth(180)
            d_lbl = QLabel(desc)
            row.addWidget(k_lbl)
            row.addWidget(d_lbl)
            sc_box.addLayout(row)

        info_layout.addWidget(shortcuts_group)
        info_layout.addStretch()
        self.tabs.addTab(info_widget, "ℹ️ Info & Hjelp")

    # Helper UI Methods
    def create_slider_group(self, parent_layout, label_text, min_v, max_v, default_v, unit):
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setFixedWidth(85)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(default_v)
        val_txt = QLabel(f"{default_v}{unit}")
        val_txt.setFixedWidth(45)
        val_txt.setAlignment(Qt.AlignmentFlag.AlignRight)
        slider.valueChanged.connect(lambda v: val_txt.setText(f"{v}{unit}"))
        row.addWidget(lbl)
        row.addWidget(slider)
        row.addWidget(val_txt)
        parent_layout.addLayout(row)
        return slider

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
        self.lbl_dl_status.setText("⏳ Installerer yt-dlp og imageio-ffmpeg …")
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
        speed_str = self.combo_speed.currentText().replace('x', '')
        rate = float(speed_str)
        self.media_player.setPlaybackRate(rate)

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
        """Clean fullscreen toggle with F/F11/Double click, maintaining UI state."""
        if not self.is_fullscreen_mode:
            self.normal_flags = self.windowFlags()
            self.normal_geometry = self.geometry()
            self.scroll_area.hide()
            self.trim_panel.hide()
            self.tabs.tabBar().hide()
            self.showFullScreen()
            self.is_fullscreen_mode = True
            self.btn_fullscreen.setText("🗗 Vindu")
        else:
            self.scroll_area.show()
            self.trim_panel.show()
            self.tabs.tabBar().show()
            self.showNormal()
            if self.normal_geometry:
                self.setGeometry(self.normal_geometry)
            self.is_fullscreen_mode = False
            self.btn_fullscreen.setText("🖥️ Fullskjerm")

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
        out_name = self.txt_name.text().strip() or "studio_pro_klipp"
        format_idx = self.combo_format.currentIndex()

        ext_map = [".mp4", ".webm", ".mkv", ".mov", ".gif",
                   ".mp3", ".ogg", ".opus", ".flac", ".wav", ".m4a"]
        out_ext = ext_map[format_idx]

        project_dir = Path(__file__).parent
        out_dir = project_dir / "resultat"
        os.makedirs(out_dir, exist_ok=True)
        output_path = str(out_dir / f"{out_name}{out_ext}")

        ffmpeg_bin = get_ffmpeg_exe()
        if not ffmpeg_bin:
            QMessageBox.critical(
                self, "FFmpeg mangler",
                f"FFmpeg ble ikke funnet.\n\nInstaller med:\n{sys.executable} -m pip install imageio-ffmpeg",
            )
            return
        cmd = [ffmpeg_bin, "-y", "-i", self.video_path]

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
            escaped_text = wm_text.replace("'", "").replace(":", "\\:")
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
            audio_filters.append(f"atempo={speed}")

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
            self.scrub_timeline(max(0, self.media_player.position() - 1000))
        elif event.key() == Qt.Key.Key_Right:
            self.scrub_timeline(min(self.duration_ms, self.media_player.position() + 1000))
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
