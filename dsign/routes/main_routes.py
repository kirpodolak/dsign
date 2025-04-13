from flask import Blueprint, render_template, redirect, url_for, flash, send_from_directory, current_app, session
from flask_login import login_required
from datetime import datetime
from dsign.forms import SettingsForm, UploadLogoForm, PlaylistProfileForm
from dsign.services.settings_service import SettingsService
import requests
from dsign.extensions import db
from dsign.models import PlaybackProfile

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
            # Ensure we're working with the initialized services
            if not hasattr(current_app, 'settings_service') or not hasattr(current_app, 'playlist_service'):
                current_app.logger.error("Required services not initialized")
                raise RuntimeError("Application services not available")

            with current_app.app_context():
                # Get profiles
                profiles = db.session.query(PlaybackProfile).all()
                
                # Get current settings
                current_settings = current_app.settings_service.get_current_settings()
                
                # Get playlists in the correct format
                playlists_data = current_app.playlist_service.get_all_playlists()
                playlists = {
                    'playlists': playlists_data if isinstance(playlists_data, list) else []
                }
                
                return render_template(
                    'settings.html',
                    profiles=profiles,
                    current_settings=current_settings,
                    playlists=playlists,
                    current_profile=None  # Add this if template expects it
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