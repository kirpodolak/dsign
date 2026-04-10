import time
from typing import Any, Dict, List, Optional


class ProfileManager:
    """
    Playback profile CRUD + application.

    Notes:
    - `PlaybackProfile.settings` is a DB JSON column (already a dict), do not json.dumps/loads it.
    - `PlaybackProfile.created_at` is stored as an integer unix timestamp in this project.
    """

    def __init__(self, logger, db_session, mpv_manager):
        self.logger = logger
        self.db_session = db_session
        self._mpv_manager = mpv_manager

    def _profile_to_dict(self, profile) -> Dict[str, Any]:
        return {
            "id": profile.id,
            "name": profile.name,
            "type": profile.profile_type,
            "settings": profile.settings or {},
            "created_at": profile.created_at,
        }

    def get_profile(self, profile_id: int) -> Optional[Dict[str, Any]]:
        from ..models import PlaybackProfile

        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        return self._profile_to_dict(profile) if profile else None

    def get_all_profiles(self, profile_type: Optional[str] = None) -> List[Dict[str, Any]]:
        from ..models import PlaybackProfile

        query = self.db_session.query(PlaybackProfile)
        if profile_type:
            query = query.filter_by(profile_type=profile_type)
        return [self._profile_to_dict(p) for p in query.all()]

    def create_profile(self, name: str, profile_type: str, settings: Dict[str, Any]) -> Optional[int]:
        from ..models import PlaybackProfile

        if not isinstance(settings, dict):
            return None

        # Prefer MPVManager validation if available
        try:
            if hasattr(self._mpv_manager, "_validate_settings") and not self._mpv_manager._validate_settings(settings):
                return None
        except Exception:
            return None

        profile = PlaybackProfile(
            name=name,
            profile_type=profile_type,
            settings=settings,
            created_at=int(time.time()),
        )
        self.db_session.add(profile)
        self.db_session.commit()
        return profile.id

    def update_profile(self, profile_id: int, name: str, settings: Dict[str, Any]) -> bool:
        from ..models import PlaybackProfile

        if not isinstance(settings, dict):
            return False

        try:
            if hasattr(self._mpv_manager, "_validate_settings") and not self._mpv_manager._validate_settings(settings):
                return False
        except Exception:
            return False

        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        if not profile:
            return False

        profile.name = name
        profile.settings = settings
        self.db_session.commit()
        return True

    def delete_profile(self, profile_id: int) -> bool:
        from ..models import PlaybackProfile

        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        if not profile:
            return False
        self.db_session.delete(profile)
        self.db_session.commit()
        return True

    def get_assigned_profile(self, playlist_id: int) -> Optional[Dict[str, Any]]:
        from ..models import PlaylistProfileAssignment

        assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(playlist_id=playlist_id).first()
        return self.get_profile(assignment.profile_id) if assignment else None

    def assign_profile_to_playlist(self, playlist_id: int, profile_id: int) -> bool:
        from ..models import PlaylistProfileAssignment

        assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(playlist_id=playlist_id).first()
        if assignment:
            assignment.profile_id = profile_id
        else:
            assignment = PlaylistProfileAssignment(playlist_id=playlist_id, profile_id=profile_id)
            self.db_session.add(assignment)
        self.db_session.commit()
        return True

    def apply_profile(self, profile_id: int) -> bool:
        profile = self.get_profile(profile_id)
        if not profile:
            return False
        settings = profile.get("settings") or {}
        if not isinstance(settings, dict):
            return False
        ok = bool(self._mpv_manager.update_settings(settings))
        if "panscan" not in settings:
            self._mpv_manager._send_command(
                {"command": ["set_property", "panscan", 0.0]},
                timeout=2.0,
            )
        return ok
