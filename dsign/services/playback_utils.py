import json
from pathlib import Path

class PlaybackUtils:
    @staticmethod
    def validate_json(json_str: str) -> bool:
        """Helper method to validate JSON string"""
        try:
            json.loads(json_str)
            return True
        except json.JSONDecodeError:
            return False

    @staticmethod
    def create_playlist_file(upload_folder: Path, tmp_dir: Path, playlist) -> Path:
        """
        Create temporary playlist file with per-item durations.

        We use FFconcat format because mpv does not support per-entry duration in plain playlists
        (and directives like #EXTVLCOPT are VLC-specific).
        """
        playlist_file = tmp_dir / f'playlist_{playlist.id}.ffconcat'

        entries = []
        missing = []
        for item in playlist.files:
            file_path = upload_folder / item.file_name
            if not file_path.exists():
                missing.append(str(file_path))
                continue

            try:
                duration = int(getattr(item, 'duration', 0) or 0)
            except Exception:
                duration = 0
            entries.append((file_path, duration))

        if not entries:
            raise ValueError(
                f"Playlist {getattr(playlist, 'id', 'unknown')} has no existing media files. "
                f"Missing: {', '.join(missing[:10])}" + (" ..." if len(missing) > 10 else "")
            )

        with open(playlist_file, 'w', encoding='utf-8') as f:
            f.write("ffconcat version 1.0\n")
            for file_path, duration in entries:
                # Escape single quotes for ffconcat quoting
                safe_path = str(file_path).replace("'", r"'\''")
                f.write(f"file '{safe_path}'\n")
                if duration > 0:
                    f.write(f"duration {duration}\n")

            # FFconcat applies the last duration only when there is a subsequent file.
            # Repeat the last file without a duration so the last item duration is respected.
            if entries:
                safe_path = str(entries[-1][0]).replace("'", r"'\''")
                f.write(f"file '{safe_path}'\n")

        return playlist_file
