import json
from datetime import datetime
from typing import Dict, List, Optional

class ProfileManager:
    def __init__(self, logger, db_session, mpv_manager):
        self.logger = logger
        self.db_session = db_session
        self._mpv_manager = mpv_manager

    def get_profile(self, profile_id: int) -> Optional[Dict]:
        """Get profile by ID"""
        from ..models import PlaybackProfile
        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        if profile:
            return {
                'id': profile.id,
                'name': profile.name,
                'type': profile.profile_type,
                'settings': json.loads(profile.settings),
                'created_at': profile.created_at.isoformat()
            }
        return None

    def get_all_profiles(self, profile_type: str = None) -> List[Dict]:
        """Get all profiles"""
        from ..models import PlaybackProfile
        query = self.db_session.query(PlaybackProfile)
        if profile_type:
            query = query.filter_by(profile_type=profile_type)
        return [{
            'id': p.id,
            'name': p.name,
            'type': p.profile_type,
            'settings': json.loads(p.settings),
            'created_at': p.created_at.isoformat()
        } for p in query.all()]

    def create_profile(self, name: str, profile_type: str, settings: Dict) -> Optional[int]:
        """Create new profile"""
        from ..models import PlaybackProfile
        if not self._mpv_manager._validate_settings(settings):
            return None
            
        profile = PlaybackProfile(
            name=name,
            profile_type=profile_type,
            settings=json.dumps(settings),
            created_at=datetime.utcnow()
        )
        self.db_session.add(profile)
        self.db_session.commit()
        return profile.id

    def update_profile(self, profile_id: int, name: str, settings: Dict) -> bool:
        """Update existing profile"""
        from ..models import PlaybackProfile
        if not self._mpv_manager._validate_settings(settings):
            return False
            
        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        if profile:
            profile.name = name
            profile.settings = json.dumps(settings)
            self.db_session.commit()
            return True
        return False

    def delete_profile(self, profile_id: int) -> bool:
        """Delete profile"""
        from ..models import PlaybackProfile
        profile = self.db_session.query(PlaybackProfile).get(profile_id)
        if profile:
            self.db_session.delete(profile)
            self.db_session.commit()
            return True
        return False

    def get_assigned_profile(self, playlist_id: int) -> Optional[Dict]:
        """Get profile assigned to playlist"""
        from ..models import PlaylistProfileAssignment
        assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(
            playlist_id=playlist_id
        ).first()
        if assignment:
            return self.get_profile(assignment.profile_id)
        return None

    def assign_profile_to_playlist(self, playlist_id: int, profile_id: int) -> bool:
        """Assign profile to playlist"""
        from ..models import PlaylistProfileAssignment
        assignment = self.db_session.query(PlaylistProfileAssignment).filter_by(
            playlist_id=playlist_id
        ).first()
        
        if assignment:
            assignment.profile_id = profile_id
        else:
            assignment = PlaylistProfileAssignment(
                playlist_id=playlist_id,
                profile_id=profile_id
            )
            self.db_session.add(assignment)
        
        self.db_session.commit()
        return True

    def apply_profile(self, profile_id: int) -> bool:
        """Apply profile settings"""
        profile = self.get_profile(profile_id)
        if profile and self._mpv_manager._validate_settings(profile['settings']):
            return self._mpv_manager.update_settings(profile['settings'])
        return False
        
    def apply_profile(self, profile_id: int) -> bool:
        """Enhanced profile application with validation"""
        profile = self.get_profile(profile_id)
        if not profile:
            return False
    
        try:
            # Validate settings against schema
            if not self._validate_settings(profile['settings']):
                self.logger.error(f"Invalid settings in profile {profile_id}")
                return False
            
            # Apply settings in batches with error handling
            success = True
            applied_settings = {}
        
            for category, settings in profile['settings'].items():
                category_success = self._mpv_manager.update_settings({category: settings})
                if not category_success:
                    self.logger.warning(f"Failed to apply {category} settings")
                    success = False
                else:
                    applied_settings[category] = settings
                
            # Log applied settings
            if applied_settings:
                self.logger.info(f"Applied settings from profile {profile_id}: {applied_settings}")
            
            return success
        
        except Exception as e:
            self.logger.error(f"Error applying profile {profile_id}: {str(e)}")
            return False
            
    def _validate_settings(self, settings: Dict) -> bool:
        """Validate settings against schema and MPV capabilities"""
        if not isinstance(settings, dict):
            return False
        
        try:
            # Get supported properties from MPV
            supported = self._mpv_manager.get_supported_properties()
        
            # Check each setting
            for prop, value in settings.items():
                if prop not in supported:
                    self.logger.warning(f"Unsupported property: {prop}")
                    return False
                
                # Basic type checking
                prop_type = supported[prop]
                if prop_type == 'bool' and not isinstance(value, bool):
                    return False
                elif prop_type == 'int' and not isinstance(value, int):
                    return False
                elif prop_type == 'string' and not isinstance(value, str):
                    return False
                
            return True
        
        except Exception as e:
            self.logger.error(f"Settings validation failed: {str(e)}")
            return False
