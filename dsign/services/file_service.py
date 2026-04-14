import os
import logging
from werkzeug.utils import secure_filename
from flask import send_from_directory
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from PIL import Image
import io
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from datetime import datetime
from dsign.models import Playlist
from dsign.config.config import Config
from .logger import ServiceLogger

class FileService:
    ALLOWED_MEDIA_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'mp4', 'avi', 'webm'}
    ALLOWED_LOGO_EXTENSIONS = {'jpg', 'jpeg', 'png'}
    DEFAULT_LOGO = 'idle_logo.jpg'
    MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2MB
    MAX_MEDIA_SIZE = Config.MAX_UPLOAD_BYTES
    THUMBNAIL_CACHE = {}  # Классовый кэш для миниатюр
    THUMBNAIL_SIZE = (200, 200)  # Размер миниатюры

    def __init__(
        self, 
        upload_folder: str, 
        logger: Optional[Union[logging.Logger, ServiceLogger]] = None,
        thumbnail_service: Optional['ThumbnailService'] = None  # Добавлено
    ):
        self.upload_folder = Path(upload_folder)
        self.logger = logger or ServiceLogger(self.__class__.__name__)
        self.thumbnail_service = thumbnail_service  # Добавлено
        # Serialize transcoding work: Pi-class devices don't like parallel ffmpeg.
        self._transcode_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dsign-transcode")
        self._transcode_lock = Lock()
        # filename -> status
        # status: {state, percent, eta_sec, out_time_sec, duration_sec, speed, started_at, updated_at, message}
        self._transcode_status: Dict[str, Dict[str, Any]] = {}
        self._ensure_directories()

    def _log_error(self, message: str, exc_info: bool = True, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для логирования ошибок"""
        extra_data = {'module': 'FileService'}
        if extra:
            extra_data.update(extra)
        self.logger.error(message, exc_info=exc_info, extra=extra_data)

    def _log_info(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для информационных логов"""
        extra_data = {'module': 'FileService'}
        if extra:
            extra_data.update(extra)
        self.logger.info(message, extra=extra_data)

    def _log_warning(self, message: str, extra: Optional[Dict[str, Any]] = None):
        """Унифицированный метод для предупреждений"""
        extra_data = {'module': 'FileService'}
        if extra:
            extra_data.update(extra)
        self.logger.warning(message, extra=extra_data)

    def _ensure_directories(self) -> None:
        """Создание необходимых директорий"""
        try:
            self.upload_folder.mkdir(exist_ok=True, parents=True)
            (self.upload_folder / 'logo').mkdir(exist_ok=True)
            (self.upload_folder / 'tmp').mkdir(exist_ok=True)
        except Exception as e:
            self._log_error(f"Failed to create directories: {str(e)}", extra={'action': 'create_directories'})
            raise RuntimeError("Failed to create upload directories")

    def allowed_file(self, filename: str, file_size: int = 0) -> bool:
        """
        Проверка допустимых расширений и размера для медиафайлов
        
        Args:
            filename: Имя файла
            file_size: Размер файла в байтах (опционально)
        """
        has_valid_extension = '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in self.ALLOWED_MEDIA_EXTENSIONS
        # content_length у FileStorage часто None — не сравнивать с порогом до реального размера
        if file_size is None or file_size <= 0:
            has_valid_size = True
        else:
            has_valid_size = file_size <= self.MAX_MEDIA_SIZE
        return has_valid_extension and has_valid_size

    def allowed_logo_file(self, filename: str, file_size: int = 0) -> bool:
        """
        Проверка допустимых расширений и размера для логотипов
        
        Args:
            filename: Имя файла
            file_size: Размер файла в байтах (опционально)
        """
        has_valid_extension = '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in self.ALLOWED_LOGO_EXTENSIONS
        if file_size is None or file_size <= 0:
            has_valid_size = True
        else:
            has_valid_size = file_size <= self.MAX_LOGO_SIZE
        return has_valid_extension and has_valid_size

    def get_media_files(self, playlist_id: Optional[int] = None) -> List[dict]:
        """Получение списка медиафайлов с метаданными"""
        try:
            files = []
            for f in self.upload_folder.iterdir():
                if f.is_file() and f.suffix.lower()[1:] in self.ALLOWED_MEDIA_EXTENSIONS:
                    stat = f.stat()
                    ext = f.suffix.lower()[1:]
                    files.append({
                        'filename': f.name,
                        'path': str(f.relative_to(self.upload_folder)),
                        'size': stat.st_size,
                        'modified': stat.st_mtime,
                        'type': ext,
                        'mimetype': self._get_mime_type(ext),
                        # Convenience flag for UI (and clients) so they don't have to guess by extension.
                        'is_video': ext.lower() in {'mp4', 'avi', 'webm', 'mov', 'mkv', 'm4v'}
                    })
            return files
        except Exception as e:
            self._log_error(f"Failed to get media files: {str(e)}", extra={'action': 'get_media_files'})
            return []

    def serve_media_file(self, filename: str, thumb: bool = False):
        """
        Отдача медиафайла или его миниатюры
        
        Args:
            filename: Имя файла
            thumb: Если True - возвращает миниатюру
        """
        try:
            if thumb:
                thumb_path = self.get_media_thumbnail(filename)
                if not thumb_path:
                    raise FileNotFoundError(f"Thumbnail for {filename} not found")
                return send_from_directory(
                    os.path.dirname(thumb_path),
                    os.path.basename(thumb_path))
            
            file_path = os.path.join(self.upload_folder, filename)
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File {filename} not found")
            stat = os.stat(file_path)
        
            headers = {
                'X-File-Size': str(stat.st_size),
                'X-File-Modified': str(stat.st_mtime),
                'X-File-Type': os.path.splitext(filename)[1][1:].lower()
            }
            return send_from_directory(
                self.upload_folder,
                filename,
                headers=headers
            )
        except Exception as e:
            self._log_error(f"Failed to serve file {filename}: {str(e)}", 
                          extra={'filename': filename, 'action': 'serve_media_file'})
            raise

    def _get_mime_type(self, ext: str) -> str:
        """Вспомогательный метод для определения MIME-типа"""
        mime_map = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'mp4': 'video/mp4',
            'webm': 'video/webm',
            'avi': 'video/x-msvideo',
        }
        return mime_map.get(ext, 'application/octet-stream')

    def _upload_file_size(self, file) -> Optional[int]:
        """Размер из multipart: content_length часто None — определяем через seek."""
        cl = getattr(file, 'content_length', None)
        if cl is not None and cl > 0:
            return int(cl)
        stream = getattr(file, 'stream', None)
        if stream is None:
            return None
        try:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(0)
            return int(size) if size >= 0 else None
        except (OSError, IOError, TypeError):
            try:
                stream.seek(0)
            except Exception:
                pass
            return None

    def handle_upload(self, files: List) -> List[str]:
        """Обработка загрузки файлов"""
        saved_files = []
        for file in files:
            if not file or not file.filename:
                continue
            try:
                reported = self._upload_file_size(file)
            except Exception:
                reported = None
            if not self.allowed_file(file.filename, reported):
                self._log_warning(
                    f"Rejected upload: {file.filename}",
                    extra={'filename': file.filename, 'reported_size': reported, 'action': 'file_upload'},
                )
                continue
            try:
                filename = secure_filename(file.filename)
                if not filename:
                    continue
                file_path = self.upload_folder / filename
                file.save(str(file_path))
                actual = file_path.stat().st_size
                if actual > self.MAX_MEDIA_SIZE:
                    file_path.unlink(missing_ok=True)
                    self._log_warning(
                        f"Removed oversized file after save: {filename}",
                        extra={'filename': filename, 'size': actual, 'action': 'file_upload'},
                    )
                    continue
                saved_files.append(filename)
                self._log_info(
                    f"Successfully uploaded file: {filename}",
                    extra={'filename': filename, 'action': 'file_upload'},
                )

                # Background transcoding: normalize videos for smoother playback (Pi 3B+).
                try:
                    ext = file_path.suffix.lower().lstrip(".")
                    if Config.AUTO_TRANSCODE_VIDEOS and ext in {"mp4", "avi", "webm", "mov", "mkv", "m4v"}:
                        self._maybe_transcode_video_async(file_path)
                except Exception as e:
                    self._log_warning(
                        "Failed to schedule transcode",
                        extra={"filename": filename, "error": str(e), "action": "transcode_schedule"},
                    )
            except Exception as e:
                self._log_error(
                    f"Failed to upload file {file.filename}: {str(e)}",
                    extra={'filename': file.filename, 'action': 'file_upload'},
                )
        return saved_files

    def _probe_video(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Return a small ffprobe summary for the first video stream."""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,profile,level,pix_fmt,width,height,r_frame_rate,avg_frame_rate",
                "-of", "json",
                str(file_path),
            ]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if p.returncode != 0 or not p.stdout:
                return None
            data = json.loads(p.stdout)
            streams = data.get("streams") or []
            return streams[0] if streams else None
        except Exception:
            return None

    def _parse_fps(self, rate: str) -> Optional[float]:
        try:
            if not rate or rate == "0/0":
                return None
            num, den = rate.split("/", 1)
            num_i = float(num)
            den_i = float(den)
            if den_i == 0:
                return None
            return num_i / den_i
        except Exception:
            return None

    def _maybe_transcode_video_async(self, file_path: Path) -> None:
        """Schedule transcoding if the file doesn't match target constraints."""
        info = self._probe_video(file_path)
        if info is None:
            # If probing fails, skip to avoid breaking uploads.
            return

        target_res = getattr(Config, "TRANSCODE_TARGET_RESOLUTION", "1920x1080")
        try:
            target_w, target_h = [int(x) for x in str(target_res).lower().split("x", 1)]
        except Exception:
            target_w, target_h = 1920, 1080

        fps = self._parse_fps(info.get("avg_frame_rate") or info.get("r_frame_rate") or "")
        codec = (info.get("codec_name") or "").lower()
        pix_fmt = (info.get("pix_fmt") or "").lower()
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        level = int(info.get("level") or 0)

        # Heuristics: keep it simple and Pi-friendly.
        needs = []
        if codec != "h264":
            needs.append("codec")
        if pix_fmt not in {"yuv420p", "nv12"}:
            needs.append("pix_fmt")
        # h264 level is stored as 40, 41, 42 etc. Keep <= 4.0.
        if level and level > 40:
            needs.append("level")
        if width != target_w or height != target_h:
            needs.append("resolution")
        # If fps is missing, or differs significantly, normalize.
        target_fps = int(getattr(Config, "TRANSCODE_TARGET_FPS", 25))
        if fps is None or abs(fps - target_fps) > 0.5:
            needs.append("fps")

        if not needs:
            return

        self._log_info(
            "Scheduling background transcode",
            extra={
                "filename": file_path.name,
                "needs": needs,
                "src": {"codec": codec, "pix_fmt": pix_fmt, "w": width, "h": height, "level": level, "fps": fps},
                "dst": {"w": target_w, "h": target_h, "fps": target_fps},
                "action": "transcode_schedule",
            },
        )
        self._transcode_executor.submit(self._transcode_video_in_place, file_path, target_w, target_h, target_fps)

    def _transcode_video_in_place(self, file_path: Path, w: int, h: int, fps: int) -> None:
        """Transcode to temp file then atomically replace original."""
        tmp_dir = self.upload_folder / "tmp"
        tmp_dir.mkdir(exist_ok=True, parents=True)
        tmp_out = tmp_dir / f".{file_path.name}.transcoding.mp4"
        bak = tmp_dir / f".{file_path.name}.orig"

        # Avoid clobbering if multiple jobs happen (shouldn't with max_workers=1, but be safe).
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except Exception:
            pass

        # Determine duration for percent/ETA.
        duration_sec: Optional[float] = None
        try:
            cmd_dur = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(file_path),
            ]
            pd = subprocess.run(cmd_dur, capture_output=True, text=True, timeout=10)
            if pd.returncode == 0:
                duration_sec = float((pd.stdout or "").strip() or "0") or None
        except Exception:
            duration_sec = None

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i", str(file_path),
            # Normalize timing
            "-vsync", "cfr",
            "-r", str(int(fps)),
            # Video
            "-vf", f"scale={w}:{h}:flags=bilinear",
            "-c:v", "libx264",
            "-profile:v", "main",
            "-level", "4.0",
            "-pix_fmt", "yuv420p",
            "-preset", "veryfast",
            "-crf", "23",
            # Audio (keep simple, HDMI friendly)
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "48000",
            # Web-friendly MP4
            "-movflags", "+faststart",
            # Machine-readable progress for UI
            "-progress", "pipe:1",
            "-nostats",
            str(tmp_out),
        ]

        start = None
        try:
            import time as _t
            start = _t.time()
            with self._transcode_lock:
                self._transcode_status[file_path.name] = {
                    "state": "running",
                    "percent": 0.0,
                    "eta_sec": None,
                    "out_time_sec": 0.0,
                    "duration_sec": duration_sec,
                    "speed": None,
                    "started_at": datetime.utcnow().isoformat() + "Z",
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                    "message": "transcoding",
                }

            # Stream progress
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out_time_ms = 0
            speed = None
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = (line or "").strip()
                    if not line or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k == "out_time_ms":
                        try:
                            out_time_ms = int(v)
                        except Exception:
                            pass
                    elif k == "speed":
                        try:
                            speed = float(v.replace("x", "").strip())
                        except Exception:
                            pass
                    elif k == "progress" and v in {"end", "error"}:
                        break

                    # Update at most a few times per second
                    if duration_sec and out_time_ms > 0:
                        out_time_sec = out_time_ms / 1_000_000.0
                        percent = max(0.0, min(99.0, (out_time_sec / duration_sec) * 100.0))
                        eta_sec = None
                        if speed and speed > 0 and out_time_sec <= duration_sec:
                            eta_sec = max(0.0, (duration_sec - out_time_sec) / speed)
                        with self._transcode_lock:
                            st = self._transcode_status.get(file_path.name) or {}
                            st.update(
                                {
                                    "percent": round(percent, 2),
                                    "eta_sec": round(eta_sec, 1) if eta_sec is not None else None,
                                    "out_time_sec": round(out_time_sec, 2),
                                    "speed": speed,
                                    "updated_at": datetime.utcnow().isoformat() + "Z",
                                }
                            )
                            self._transcode_status[file_path.name] = st
            finally:
                rc = proc.wait(timeout=60 * 30)
                stderr = (proc.stderr.read() if proc.stderr else "") if proc.stderr is not None else ""

            if rc != 0 or not tmp_out.exists() or tmp_out.stat().st_size < 1024 * 50:
                self._log_warning(
                    "Transcode failed",
                    extra={
                        "filename": file_path.name,
                        "returncode": rc,
                        "stderr_tail": (stderr or "")[-2000:],
                        "action": "transcode",
                    },
                )
                with self._transcode_lock:
                    self._transcode_status[file_path.name] = {
                        **(self._transcode_status.get(file_path.name) or {}),
                        "state": "failed",
                        "message": "ffmpeg failed",
                        "updated_at": datetime.utcnow().isoformat() + "Z",
                    }
                try:
                    if tmp_out.exists():
                        tmp_out.unlink()
                except Exception:
                    pass
                return

            # Replace original (keep a backup in tmp for debugging)
            try:
                if bak.exists():
                    bak.unlink()
            except Exception:
                pass

            try:
                file_path.replace(bak)
            except Exception:
                # If replace fails, abort safely.
                self._log_warning("Failed to move original aside", extra={"filename": file_path.name, "action": "transcode"})
                return

            try:
                tmp_out.replace(file_path)
            except Exception:
                # Attempt rollback
                try:
                    if file_path.exists():
                        file_path.unlink()
                except Exception:
                    pass
                try:
                    bak.replace(file_path)
                except Exception:
                    pass
                self._log_warning("Failed to install transcoded file", extra={"filename": file_path.name, "action": "transcode"})
                with self._transcode_lock:
                    self._transcode_status[file_path.name] = {
                        **(self._transcode_status.get(file_path.name) or {}),
                        "state": "failed",
                        "message": "install failed",
                        "updated_at": datetime.utcnow().isoformat() + "Z",
                    }
                return

            dur = round((_t.time() - start), 2) if start else None
            self._log_info(
                "Transcode completed",
                extra={"filename": file_path.name, "duration_sec": dur, "action": "transcode"},
            )
            with self._transcode_lock:
                self._transcode_status[file_path.name] = {
                    **(self._transcode_status.get(file_path.name) or {}),
                    "state": "completed",
                    "percent": 100.0,
                    "eta_sec": 0.0,
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                    "message": "completed",
                }

        except Exception as e:
            self._log_warning("Transcode exception", extra={"filename": file_path.name, "error": str(e), "action": "transcode"})
            with self._transcode_lock:
                self._transcode_status[file_path.name] = {
                    **(self._transcode_status.get(file_path.name) or {}),
                    "state": "failed",
                    "message": str(e),
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                }
            try:
                if tmp_out.exists():
                    tmp_out.unlink()
            except Exception:
                pass

    def get_transcode_status(self, filename: Optional[str] = None) -> Dict[str, Any]:
        """Return current transcode status (all files or one)."""
        with self._transcode_lock:
            if filename:
                return self._transcode_status.get(filename, {})
            # Return a shallow copy so callers can't mutate internal state
            return {k: dict(v) for k, v in self._transcode_status.items()}

    def handle_logo_upload(self, logo) -> Dict[str, Any]:
        """
        Обработка загрузки логотипа с проверкой размера
        
        Returns:
            Dict: {
                'success': bool,
                'filename': str (если успешно),
                'error': str (если ошибка)
            }
        """
        if not logo:
            return {'success': False, 'error': 'No file provided'}
            
        if not self.allowed_logo_file(logo.filename, logo.content_length):
            error_msg = 'Invalid file type or size exceeds limit (max 2MB)'
            self._log_warning(error_msg, extra={
                'action': 'logo_upload',
                'filename': logo.filename,
                'file_size': logo.content_length
            })
            return {'success': False, 'error': error_msg}

        try:
            filename = self.DEFAULT_LOGO
            file_path = self.upload_folder / filename
            
            logo.save(file_path)
            self._log_info("Logo uploaded successfully", 
                         extra={'filename': filename, 'action': 'logo_upload'})
            
            return {
                'success': True,
                'filename': filename,
                'message': 'Logo updated successfully'
            }
        except Exception as e:
            error_msg = f"Failed to upload logo: {str(e)}"
            self._log_error(error_msg, extra={'action': 'logo_upload'})
            return {'success': False, 'error': error_msg}

    def delete_files(self, filenames: List[str]) -> Dict[str, Any]:
        """Удаление файлов"""
        deleted = []
        failed = []
        
        for filename in filenames:
            try:
                file_path = self.upload_folder / secure_filename(filename)
                if file_path.exists():
                    file_path.unlink()
                    deleted.append(filename)
                    self._log_info(f"Deleted file: {filename}", 
                                 extra={'filename': filename, 'action': 'delete_file'})
                else:
                    failed.append(filename)
            except Exception as e:
                failed.append(filename)
                self._log_error(f"Failed to delete file {filename}: {str(e)}", 
                               extra={'filename': filename, 'action': 'delete_file'})
        
        return {
            "deleted": deleted,
            "count": len(deleted),
            "failed": failed
        }

    def get_logo_path(self) -> str:
        """Получение пути к логотипу с проверкой существования"""
        logo_path = self.upload_folder / self.DEFAULT_LOGO
        if not logo_path.exists():
            raise FileNotFoundError(f"Custom logo not found at {logo_path}")
        return str(logo_path)

    def get_logo_size(self) -> int:
        """Получение размера логотипа в байтах"""
        logo_path = self.upload_folder / self.DEFAULT_LOGO
        if logo_path.exists():
            return logo_path.stat().st_size
        return 0
        
    def get_media_thumbnail(self, filename: str) -> Optional[str]:
        """Получение пути к миниатюре через ThumbnailService"""
        if not hasattr(self, 'thumbnail_service') or not self.thumbnail_service:
            self._log_warning("Thumbnail service not available", 
                            extra={'filename': filename})
            return None
    
        try:
            thumb_path = self.thumbnail_service.generate_thumbnail(filename)
            return str(thumb_path) if thumb_path else None
        except Exception as e:
            self._log_error(f"Thumbnail error: {str(e)}", 
                          extra={'filename': filename, 'action': 'generate_thumbnail'})
            return None
            
    def get_media_files_with_playlist_info(self, playlist_id=None, db_session=None):
        """Получает файлы с информацией о принадлежности к плейлисту"""
        try:
            all_files = self.get_media_files()

            if not playlist_id or playlist_id == 'all':
                return all_files

            # Редактор плейлиста: idle logo не является медиа слайда — не показывать в списке.
            logo_l = self.DEFAULT_LOGO.lower()
            all_files = [f for f in all_files if f.get('filename', '').lower() != logo_l]

            if not db_session:
                raise RuntimeError("Database session not provided")
            
            playlist_files = set()
            duration_by_name: dict = {}
            muted_by_name: dict = {}
            playlist = db_session.query(Playlist).get(playlist_id)
            if playlist:
                for pf in playlist.files or []:
                    playlist_files.add(pf.file_name)
                    # Persisted display duration for images (videos often store 0); UI reads this key.
                    duration_by_name[pf.file_name] = pf.duration
                    muted_by_name[pf.file_name] = bool(getattr(pf, "muted", False))

            return [{
                **file,
                'included': file['filename'] in playlist_files,
                # Sync with playlist_files.duration from DB (null if file not in playlist).
                'duration': duration_by_name.get(file['filename']),
                'muted': muted_by_name.get(file['filename'], False),
                # `is_video` is already provided by get_media_files(); keep it stable if present.
                'is_video': bool(file.get('is_video')) or file.get('type', '').lower() in {'mp4', 'avi', 'webm', 'mov', 'mkv', 'm4v'}
            } for file in all_files]
            
        except Exception as e:
            self._log_error(f"Error in get_media_files_with_playlist_info: {str(e)}", 
                          extra={'playlist_id': playlist_id, 'action': 'get_playlist_files'})
            raise
