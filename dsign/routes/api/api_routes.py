from threading import Lock
import os
import shutil
import subprocess
import traceback
import time
from pathlib import Path 
from flask import jsonify, request, send_from_directory, abort, current_app, send_file
from flask_login import login_required, current_user, login_user, logout_user
from flask_wtf.csrf import validate_csrf
from werkzeug.utils import secure_filename
from dsign.models import PlaybackProfile, PlaylistProfileAssignment, Playlist, User, db
from dsign.config.mpv_settings_schema import MPV_SETTINGS_SCHEMA
from PIL import Image
from dsign.config.config import THUMBNAIL_FOLDER, THUMBNAIL_URL
from dsign.services import ThumbnailService
from dsign.extensions import bcrypt

thumbnail_lock = Lock()

def init_api_routes(api_bp, services):
    settings_service = services.get('settings_service')
    playback_service = services.get('playback_service')
    playlist_service = services.get('playlist_service')
    file_service = services.get('file_service')
    thumbnail_service = services.get('thumbnail_service')
    socketio = services.get('socketio')
    UPLOAD_LOGO_NAME = "idle_logo.jpg"
    MAX_LOGO_SIZE = 5 * 1024 * 1024  # 5MB

    # ======================
    # MPV Settings (/api/settings)
    # ======================
    @api_bp.route('/settings/schema', methods=['GET'])
    @login_required
    def get_settings_schema():
        try:
            return jsonify({
                'success': True,
                'schema': MPV_SETTINGS_SCHEMA
            })
        except Exception as e:
            current_app.logger.error(f"Error getting settings schema: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'Failed to load settings schema'
            }), 500

    @api_bp.route('/settings/current', methods=['GET'])
    @login_required
    def get_current_settings():
        try:
            settings = settings_service.get_current_settings()
            profile = None
            
            # Get current profile if available
            if settings.get('profile_id'):
                profile = db.session.query(PlaybackProfile).get(settings['profile_id'])
            
            return jsonify({
                'success': True,
                'settings': settings,
                'profile': {
                    'id': profile.id if profile else None,
                    'name': profile.name if profile else None,
                    'type': profile.profile_type if profile else None
                }
            })
        except Exception as e:
            current_app.logger.error(f"Error getting current settings: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/settings/update', methods=['POST'])
    @login_required
    def update_settings():
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'error': 'No data provided'
                }), 400

            # Validate and update settings
            success = settings_service.update_mpv_settings(
                data,
                profile_type=data.get('profile_type'),
                playlist_id=data.get('playlist_id')
            )

            if success:
                return jsonify({'success': True})
            else:
                return jsonify({
                    'success': False,
                    'error': 'Failed to update settings'
                }), 500

        except Exception as e:
            current_app.logger.error(f"Error updating settings: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    # ======================
    # Playback Profiles (/api/profiles)
    # ======================
    @api_bp.route('/profiles', methods=['GET'])
    @login_required
    def get_profiles():
        try:
            profiles = db.session.query(PlaybackProfile).all()
            return jsonify({
                'success': True,
                'profiles': [{
                    'id': p.id,
                    'name': p.name,
                    'type': p.profile_type,
                    'settings': p.settings
                } for p in profiles]
            })
        except Exception as e:
            current_app.logger.error(f"Error getting profiles: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles', methods=['POST'])
    @login_required
    def create_profile():
        try:
            data = request.get_json()
            if not data or 'name' not in data or 'type' not in data:
                return jsonify({
                    'success': False,
                    'error': 'Missing required fields'
                }), 400

            # Validate profile type
            if data['type'] not in ['idle', 'playlist']:
                return jsonify({
                    'success': False,
                    'error': 'Invalid profile type'
                }), 400

            # Create new profile
            profile = PlaybackProfile(
                name=data['name'],
                profile_type=data['type'],
                settings=data.get('settings', {})
            )
            db.session.add(profile)
            db.session.commit()

            return jsonify({
                'success': True,
                'profile_id': profile.id
            }), 201

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating profile: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/<int:profile_id>', methods=['DELETE'])
    @login_required
    def delete_profile(profile_id):
        try:
            profile = db.session.query(PlaybackProfile).get(profile_id)
            if not profile:
                return jsonify({
                    'success': False,
                    'error': 'Profile not found'
                }), 404

            # Remove any assignments first
            db.session.query(PlaylistProfileAssignment).filter_by(
                profile_id=profile_id
            ).delete()

            db.session.delete(profile)
            db.session.commit()

            return jsonify({'success': True})

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting profile: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/assign', methods=['POST'])
    @login_required
    def assign_profile():
        try:
            data = request.get_json()
            if not data or 'playlist_id' not in data:
                return jsonify({
                    'success': False,
                    'error': 'Missing playlist_id'
                }), 400

            # Remove existing assignment if exists
            db.session.query(PlaylistProfileAssignment).filter_by(
                playlist_id=data['playlist_id']
            ).delete()

            # Add new assignment if profile_id provided
            if data.get('profile_id'):
                # Verify profile exists
                profile = db.session.query(PlaybackProfile).get(data['profile_id'])
                if not profile or profile.profile_type != 'playlist':
                    return jsonify({
                        'success': False,
                        'error': 'Invalid playlist profile'
                    }), 400

                assignment = PlaylistProfileAssignment(
                    playlist_id=data['playlist_id'],
                    profile_id=data['profile_id']
                )
                db.session.add(assignment)

            db.session.commit()

            # Notify clients of the change
            if socketio:
                socketio.emit('profile_assignment_changed', {
                    'playlist_id': data['playlist_id'],
                    'profile_id': data.get('profile_id')
                })

            return jsonify({'success': True})

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error assigning profile: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/apply/<int:profile_id>', methods=['POST'])
    @login_required
    def apply_profile(profile_id):
        try:
            profile = db.session.query(PlaybackProfile).get(profile_id)
            if not profile:
                return jsonify({
                    'success': False,
                    'error': 'Profile not found'
                }), 404

            # Apply profile settings
            settings_service.apply_profile(profile.settings)

            # Update current profile reference
            settings_service.update_current_profile(profile_id)

            return jsonify({
                'success': True,
                'profile_id': profile_id
            })

        except Exception as e:
            current_app.logger.error(f"Error applying profile: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/assignments', methods=['GET'])
    @login_required
    def get_profile_assignments():
        try:
            assignments = db.session.query(PlaylistProfileAssignment).all()
            return jsonify({
                "success": True,
                "assignments": {a.playlist_id: a.profile_id for a in assignments}
            })
        except Exception as e:
            current_app.logger.error(f"Error getting assignments: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Failed to load assignments"
            }), 500

    # ======================
    # Playback Control (/api/playback)
    # ======================
    @api_bp.route('/playback/play', methods=['POST'])
    @login_required
    def playback_play():
        try:
            data = request.get_json()
            if not data or 'playlist_id' not in data:
                return jsonify({
                    "success": False,
                    "error": "Missing playlist_id"
                }), 400

            # Get additional settings from request if provided
            additional_settings = data.get('settings', {})
        
            # Start playback through service
            result = playback_service.play(
                playlist_id=data['playlist_id'],
                settings=additional_settings
            )
        
            return jsonify({
                "success": True,
                "playlist_id": data['playlist_id'],
                "details": result
            })
        except Exception as e:
            current_app.logger.error(f"Error starting playback: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playback/stop', methods=['POST'])
    @login_required
    def playback_stop():
        try:
            result = playback_service.stop()
            return jsonify({
                "success": True,
                "details": result
            })
        except Exception as e:
            current_app.logger.error(f"Error stopping playback: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playback/status', methods=['GET'])
    @login_required
    def playback_status():
        try:
            status = playback_service.get_status()
            status_dict = status if isinstance(status, dict) else vars(status)
            return jsonify({
                "success": True,
                "status": status_dict
            })
        except Exception as e:
            current_app.logger.error(f"Error getting playback status: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    # ======================
    # Playlist Management (/api/playlists)
    # ======================
    @api_bp.route('/playlists', methods=['GET'])
    @login_required
    def get_playlists():
        try:
            playlists = db.session.query(Playlist).all()
            assignments = {a.playlist_id: a.profile_id 
                          for a in db.session.query(PlaylistProfileAssignment).all()}
        
            return jsonify({
                "success": True,
                "playlists": [{
                    **p.to_dict(),
                    "profile_id": assignments.get(p.id)
                } for p in playlists]
            })
        except Exception as e:
            current_app.logger.error(f"Error getting playlists: {str(e)}")
            return jsonify({
                "success": False,
                 "error": "Failed to load playlists"
            }), 500

    @api_bp.route('/playlists', methods=['POST'])
    @login_required
    def create_playlist():
        try:
            data = request.get_json()
            if not data or 'name' not in data:
                return jsonify({
                    "success": False,
                    "error": "Missing required fields"
                }), 400

            result = playlist_service.create_playlist(data)
            if socketio:
                socketio.emit('playlist_created', {
                    'playlist_id': result['playlist_id'],
                    'name': data['name']
                })
            return jsonify({
                "success": True,
                "playlist_id": result["playlist_id"]
            }), 201
        except Exception as e:
            current_app.logger.error(f"Error creating playlist: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>', methods=['GET'])
    @login_required
    def get_playlist(playlist_id):
        try:
            playlist = playlist_service.get_playlist(playlist_id)
            if not playlist:
                return jsonify({
                    "success": False,
                    "error": "Playlist not found"
                }), 404
            
            return jsonify({
                "success": True,
                "playlist": playlist
            })
        except Exception as e:
            current_app.logger.error(f"Error getting playlist: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>', methods=['PUT'])
    @login_required
    def update_playlist(playlist_id):
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    "success": False,
                    "error": "No data provided"
                }), 400

            result = playlist_service.update_playlist(playlist_id, data)
            
            # Логирование успешного обновления
            if result.get('success'):
                current_app.logger.info(f"Playlist {playlist_id} metadata updated successfully")
                if 'name' in data:
                    current_app.logger.debug(f"M3U file regenerated for playlist {playlist_id}")
            
            return jsonify(result)
        except Exception as e:
            current_app.logger.error(f"Error updating playlist {playlist_id}: {str(e)}", exc_info=True)
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>', methods=['DELETE'])
    @login_required
    def delete_playlist(playlist_id):
        try:
            # Получаем имя плейлиста перед удалением для удаления M3U файла
            playlist = db.session.query(Playlist).get(playlist_id)
            playlist_name = playlist.name if playlist else None

            # Удаляем привязки к профилям
            db.session.query(PlaylistProfileAssignment).filter_by(
                playlist_id=playlist_id
            ).delete()

            result = playlist_service.delete_playlist(playlist_id)
            
            # Удаление M3U файла через сервис
            if result.get('success') and playlist_name:
                try:
                    playlist_service._delete_m3u_file(playlist_name)
                    current_app.logger.info(f"M3U file deleted for playlist {playlist_id}")
                except Exception as e:
                    current_app.logger.warning(f"Could not delete M3U file for playlist {playlist_id}: {str(e)}")

            if socketio:
                socketio.emit('playlist_deleted', {'playlist_id': playlist_id})
                
            return jsonify(result)
        except Exception as e:
            current_app.logger.error(f"Error deleting playlist {playlist_id}: {str(e)}", exc_info=True)
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/active', methods=['GET'])
    @login_required
    def active_playlist():
        try:
            active_playlist = playlist_service.get_active_playlist()
            return jsonify({
                "success": True,
                "active": bool(active_playlist),
                "playlist": active_playlist
            })
        except Exception as e:
            current_app.logger.error(f"Error getting active playlist: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>/reorder', methods=['POST'])
    @login_required
    def reorder_playlist_items(playlist_id):
        try:
            data = request.get_json()
            if not data or 'item_id' not in data or 'position' not in data:
                return jsonify({
                    "success": False,
                    "error": "Invalid data"
                }), 400

            success = playlist_service.reorder_single_item(
                playlist_id,
                data['item_id'],
                data['position']
            )
            return jsonify({"success": success})
        except Exception as e:
            current_app.logger.error(f"Error reordering playlist: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500
    
    @api_bp.route('/playlists/<int:playlist_id>/files', methods=['POST'])
    @login_required
    def update_playlist_files(playlist_id):
        try:
            data = request.get_json()
            if not data or 'files' not in data:
                return jsonify({"success": False, "error": "Missing files data"}), 400

            current_app.logger.debug(f"Updating files for playlist {playlist_id} with {len(data.get('files', []))} files")

            result = playlist_service.update_playlist_files(playlist_id, data.get('files', []))
            
            if not result.get('success'):
                current_app.logger.error(f"Playlist files update failed: {result.get('error')}")
                return jsonify(result), 400

            current_app.logger.info(f"Playlist {playlist_id} files updated successfully")
            current_app.logger.debug(f"M3U file regenerated for playlist {playlist_id}")
            
            return jsonify(result)
        
        except Exception as e:
            current_app.logger.error(f"API error updating playlist files {playlist_id}: {str(e)}", exc_info=True)
            return jsonify({"success": False, "error": "Internal server error"}), 500
    
    # ======================
    # Media File Handling (/api/media)
    # ======================
    @api_bp.route('/media/files', methods=['GET'])
    @login_required
    def get_media_files():
        try:
            playlist_id = request.args.get('playlist_id')
            files = file_service.get_media_files_with_playlist_info(
                playlist_id=playlist_id,
                db_session=db.session
            )
        
            return jsonify({
                "success": True,
                "files": files,
                "count": len(files)
            })
        except Exception as e:
            current_app.logger.error(f"Error getting media files: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500


    @api_bp.route('/media/files', methods=['POST'])
    @login_required
    def delete_media_files():
        try:
            data = request.get_json()
            if not data or 'files' not in data or not isinstance(data['files'], list):
                return jsonify({
                    "success": False,
                    "error": "Invalid request format. Expected {'files': [...]}"
                }), 400

            files_to_delete = [secure_filename(f) for f in data['files'] if f]
            if not files_to_delete:
                return jsonify({
                    "success": False,
                    "error": "No valid filenames provided"
                }), 400

            result = file_service.delete_files(files_to_delete)
            return jsonify({
                "success": True,
                "deleted": result
            })
        except Exception as e:
            current_app.logger.error(f"Error deleting media files: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/media/upload', methods=['POST'])
    @login_required
    def upload_media():
        try:
            if 'files' not in request.files:
                return jsonify({
                    "success": False,
                    "error": "No files provided"
                }), 400

            saved_files = file_service.handle_upload(request.files.getlist('files'))
            return jsonify({
                "success": True,
                "files": saved_files
            })
        except Exception as e:
            current_app.logger.error(f"Error uploading media: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/media/upload_logo', methods=['POST'])
    @login_required
    def upload_logo():
        try:
            if 'logo' not in request.files:
                return jsonify({"success": False, "error": "No file provided"}), 400

            file = request.files['logo']
            if not file.filename:
                return jsonify({"success": False, "error": "Empty filename"}), 400

            # Проверка размера и формата файла
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
        
            if file_size > current_app.config['MAX_LOGO_SIZE']:
                return jsonify({
                    "success": False,
                    "error": f"File too large (max {current_app.config['MAX_LOGO_SIZE']//1024//1024}MB)"
                }), 400

            try:
                img = Image.open(file.stream)
                img.verify()
                file.stream.seek(0)
                if img.format.lower() not in ('jpeg', 'jpg', 'png'):
                    raise ValueError("Unsupported format")
            except Exception:
                return jsonify({
                    "success": False,
                    "error": "Invalid image file (only JPEG/PNG allowed)"
                }), 400

            # Сохранение файла
            filename = secure_filename(current_app.config['IDLE_LOGO'])
            upload_folder = current_app.config['UPLOAD_FOLDER']
            file_path = os.path.join(upload_folder, filename)
        
            # Создаем backup
            backup_path = None
            if os.path.exists(file_path):
                backup_path = f"{file_path}.bak"
                os.rename(file_path, backup_path)

            try:
                file.save(file_path)
                os.chmod(file_path, 0o644)

                # Обновляем логотип в плеере
                if not playback_service.restart_idle_logo(upload_folder=upload_folder, idle_logo=filename):
                    raise RuntimeError("Failed to update player")

                # Успешное завершение
                if backup_path and os.path.exists(backup_path):
                    os.unlink(backup_path)

                return jsonify({
                    "success": True,
                    "message": "Logo updated successfully",
                    "timestamp": int(time.time())
                })

            except Exception as e:
                # Восстановление из backup
                if backup_path and os.path.exists(backup_path):
                    if os.path.exists(file_path):
                        os.unlink(file_path)
                    os.rename(backup_path, file_path)
                    playback_service.restart_idle_logo(upload_folder=upload_folder, idle_logo=filename)

                current_app.logger.error(f"Logo upload failed: {str(e)}")
                return jsonify({
                    "success": False,
                    "error": str(e),
                    "recovered": backup_path is not None
                }), 500

        except Exception as e:
            current_app.logger.error(f"Unexpected error: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Internal server error"
            }), 500

    @api_bp.route('/media/logo_status', methods=['GET'])
    @login_required
    def get_logo_status():
        try:
            logo_path = playback_service.get_current_logo_path()
            return jsonify({
                "success": True,
                "path": str(logo_path),
                "is_default": "placeholder.jpg" in str(logo_path),
                "exists": True,
                "filename": os.path.basename(logo_path)
            })
        except FileNotFoundError:
            return jsonify({
                "success": False,
                "error": "No logo files available",
                "exists": False
            }), 404
        except Exception as e:
            current_app.logger.error(f"Error getting logo status: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/media/<path:filename>', methods=['GET'])
    def serve_media(filename):
        try:
            upload_folder = current_app.config.get('UPLOAD_FOLDER', '/var/lib/dsign/media')
            file_path = os.path.join(upload_folder, filename)
        
            if not os.path.exists(file_path) or not os.path.isfile(file_path):
                abort(404)
            
            return send_from_directory(
                upload_folder,
                filename,
                mimetype=None,
                as_attachment=False,
                conditional=True
            )
        except Exception as e:
            current_app.logger.error(f"Error serving media file: {str(e)}")
            abort(404)
                
    @api_bp.route('/media/mpv_screenshot', methods=['GET'])
    def get_mpv_screenshot():
        try:
            screenshot_path = os.path.join(current_app.config['STATIC_FOLDER'], 'images', 'on_air_screen.jpg')
            default_path = os.path.join(current_app.config['STATIC_FOLDER'], 'images', 'default-preview.jpg')
        
            # Check if screenshot exists and is a valid image
            if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 1024:
                try:
                    with Image.open(screenshot_path) as img:
                        img.verify()
                    return send_file(screenshot_path, mimetype='image/jpeg')
                except:
                    current_app.logger.warning("Invalid screenshot image, using default")
        
            return send_file(default_path, mimetype='image/jpeg')
        
        except Exception as e:
            current_app.logger.error(f"Screenshot error: {str(e)}")
            abort(500)
            
    @api_bp.route('/media/mpv_screenshot/capture', methods=['POST'])
    @login_required
    def force_screenshot_update():
        """Запуск systemd-сервиса для обновления скриншота"""
        try:
            # Check if CSRF token is present in form data or headers
            csrf_token = request.form.get('csrf_token') or request.headers.get('X-CSRFToken')
            if not csrf_token:
                current_app.logger.error("CSRF token missing")
                abort(403, description="CSRF token missing")
        
            try:
                validate_csrf(csrf_token)
            except Exception as e:
                current_app.logger.error(f"CSRF validation failed: {str(e)}")
                abort(403, description="Invalid CSRF token")

            # Import subprocess here to avoid UnboundLocalError
            import subprocess
        
            # Start service
            result = subprocess.run(
                ['sudo', '/bin/systemctl', 'start', 'screenshot.service'],
                check=True,
                timeout=10,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
    
            current_app.logger.info(f"Screenshot service started: {result.stdout}")
            return jsonify({"success": True})

        except subprocess.TimeoutExpired as e:
            current_app.logger.error(f"Service timeout: {e.stderr}")
            return jsonify({"success": False, "error": "Service timeout"}), 500
    
        except subprocess.CalledProcessError as e:
            current_app.logger.error(f"Service failed: {e.stderr}")
            return jsonify({"success": False, "error": "Service failed"}), 500
    
        except Exception as e:
            current_app.logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            return jsonify({"success": False, "error": "Internal error"}), 500
            
    @api_bp.route('/media/thumbnail/<filename>', methods=['GET'])
    def get_media_thumbnail(filename):
        try:
            # Всегда используем .jpg в пути
            thumb_name = f"thumb_{Path(filename).stem}.jpg"
        
            # Пробуем отдать готовую миниатюру
            thumb_path = current_app.thumbnail_service.thumbnail_folder / thumb_name
            if thumb_path.exists():
                return send_from_directory(
                    current_app.thumbnail_service.thumbnail_folder,
                    thumb_name,
                    mimetype='image/jpeg',
                    max_age=86400
                )
        
            # Генерация новой миниатюры
            thumb_path = current_app.thumbnail_service.generate_thumbnail(filename)
            if thumb_path:
                return send_from_directory(
                    current_app.thumbnail_service.thumbnail_folder,
                    thumb_path.name,
                    mimetype='image/jpeg',
                    max_age=86400
                )
            
        except Exception as e:
            current_app.logger.error(f"Thumbnail error: {str(e)}")
        
        # Fallback
        return send_from_directory(
            current_app.static_folder,
            'images/default-preview.jpg',
            max_age=3600
        )
            
    @api_bp.route('/debug/thumbnails', methods=['GET'])
    @login_required
    def list_thumbnails():
        """Debug endpoint to list generated thumbnails"""
        thumb_dir = current_app.thumbnail_service.thumbnail_folder
        thumbs = []
        for f in thumb_dir.glob('thumb_*'):
            thumbs.append({
                'name': f.name,
                'size': f.stat().st_size,
                'modified': f.stat().st_mtime
            })
        return jsonify({'thumbnails': thumbs})

    @api_bp.route('/debug/thumbnail/<filename>', methods=['GET'])
    @login_required
    def get_thumbnail_debug(filename):
        """Debug endpoint to inspect a specific thumbnail"""
        thumb_path = current_app.thumbnail_service.thumbnail_folder / f"thumb_{filename}"
        if not thumb_path.exists():
            return jsonify({'error': 'Thumbnail not found'}), 404
        
        try:
            with Image.open(thumb_path) as img:
                return jsonify({
                    'filename': filename,
                    'size': thumb_path.stat().st_size,
                    'format': img.format,
                    'mode': img.mode,
                    'width': img.width,
                    'height': img.height
                })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
