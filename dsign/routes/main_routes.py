from flask import Blueprint, render_template, redirect, url_for, flash, send_from_directory, current_app, session
from flask_login import login_required
from datetime import datetime
from dsign.forms import SettingsForm, UploadLogoForm, PlaylistProfileForm
from dsign.services.settings_service import SettingsService
import requests
from dsign.extensions import db
from dsign.models import PlaybackProfile, PlaylistProfileAssignment

def init_main_routes(main_bp: Blueprint, settings_service: SettingsService):
    
    @main_bp.route('/')
    @login_required
    def index():
        settings = settings_service.get_current_settings()
        
        return render_template(
            'index.html',
            settings=settings,
            default_logo_cache_buster=int(datetime.now().timestamp())
        )

    @main_bp.route('/settings')
    @login_required
    def settings():
        try:
            # Get profiles with their assignments
            profiles = db.session.query(PlaybackProfile).all()
            
            # Get current settings
            current_settings = settings_service.get_current_settings()
            
            # Get playlists with their assigned profiles
            playlists = db.session.query(Playlist).all()
            playlist_data = []
            
            for playlist in playlists:
                assignment = db.session.query(PlaylistProfileAssignment).filter_by(
                    playlist_id=playlist.id
                ).first()
                
                playlist_data.append({
                    'id': playlist.id,
                    'name': playlist.name,
                    'profile_id': assignment.profile_id if assignment else None
                })
            
            # Get current profile
            current_profile = None
            if current_settings.get('profile_id'):
                current_profile = db.session.query(PlaybackProfile).get(
                    current_settings['profile_id']
                )
            
            return render_template(
                'settings.html',
                profiles=profiles,
                current_settings=current_settings,
                playlists={'playlists': playlist_data},
                current_profile=current_profile
            )
                
        except Exception as e:
            current_app.logger.error(f"Settings route error: {str(e)}", exc_info=True)
            return render_template(
                'settings.html',
                profiles=[],
                current_settings={},
                playlists={'playlists': []},
                current_profile=None
            ), 500
    
    @main_bp.route('/favicon.ico')
    def favicon():
        return "", 204

    @main_bp.route('/gallery')
    @login_required
    def gallery():
        """Рендеринг галереи медиа"""
        return render_template('gallery.html')

    @main_bp.route('/playlist')
    @login_required
    def playlist():
        """Рендеринг страницы плейлистов"""
        return render_template('playlist.html')
