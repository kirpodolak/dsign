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
        """Create temporary playlist file"""
        playlist_file = tmp_dir / f'playlist_{playlist.id}.txt'
        with open(playlist_file, 'w') as f:
            for file in playlist.files:
                file_path = upload_folder / file.file_name
                if file_path.exists():
                    f.write(f"file '{file_path}'\n")
        return playlist_file
