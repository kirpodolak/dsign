import os
from flask import jsonify, request, send_from_directory, abort, current_app
from flask_login import login_required
from werkzeug.utils import secure_filename
from dsign.models import PlaybackProfile, PlaylistProfileAssignment, db
from dsign.config.mpv_settings_schema import MPV_SETTINGS_SCHEMA

def init_api_routes(api_bp, services):
    settings_service = services.get('settings_service')
    playback_service = services.get('playback_service')
    playlist_service = services.get('playlist_service')
    file_service = services.get('file_service')
    socketio = services.get('socketio')
    UPLOAD_LOGO_NAME = "idle_logo.jpg"

    # ======================
    # MPV Settings (/api/settings)
    # ======================
    @api_bp.route('/settings/schema', methods=['GET'])
    @login_required
    def get_settings_schema():
        return jsonify({
            'success': True,
            'schema': MPV_SETTINGS_SCHEMA
        })

    @api_bp.route('/settings/current', methods=['GET'])
    @login_required
    def get_current_settings():
        try:
            settings = settings_service.get_current_settings()
            return jsonify({
                'success': True,
                'settings': settings
            })
        except Exception as e:
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
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/assign', methods=['POST'])
    @login_required
    def assign_profile():
        try:
            data = request.get_json()
        
            if 'playlist_id' in data:
                PlaylistProfileAssignment.query.filter_by(
                    playlist_id=data['playlist_id']
                ).delete()
            
                if data.get('profile_id'):
                    assignment = PlaylistProfileAssignment(
                        playlist_id=data['playlist_id'],
                        profile_id=data['profile_id']
                    )
                    db.session.add(assignment)
            
                db.session.commit()
                return jsonify({'success': True})
        
            elif 'profile_id' in data:
                profile = PlaybackProfile.query.get(data['profile_id'])
                if not profile:
                    return jsonify({'success': False, 'error': 'Profile not found'}), 404
                
                settings_service.apply_profile(profile.settings)
                return jsonify({'success': True})
            
            return jsonify({
                'success': False,
                'error': 'Either playlist_id or profile_id required'
            }), 400
        
        except Exception as e:
            db.session.rollback()
            return jsonify({
                'success': False,
                'error': str(e)
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

            result = playback_service.play(
                playlist_id=data['playlist_id'],
                settings=data.get('settings', {})
            )
            
            return jsonify({
                "success": True,
                "playlist_id": data['playlist_id'],
                "details": result
            })
        except Exception as e:
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
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    # ======================
    # Playlist Management (/api/playlists)
    # ======================
    @api_bp.route('/playlists', methods=['GET', 'POST'])
    @login_required
    def manage_playlists():
        if request.method == 'GET':
            try:
                return jsonify({
                    "success": True,
                    "playlists": playlist_service.get_all_playlists()
                })
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        else:
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
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500

    @api_bp.route('/playlists/<int:playlist_id>', methods=['GET', 'PUT', 'DELETE'])
    @login_required
    def manage_playlist(playlist_id):
        try:
            if request.method == 'GET':
                return jsonify({
                    "success": True,
                    "playlist": playlist_service.get_playlist(playlist_id)
                })
            elif request.method == 'PUT':
                data = request.get_json()
                if not data:
                    return jsonify({
                        "success": False,
                        "error": "No data provided"
                    }), 400
                return jsonify(
                    playlist_service.update_playlist(playlist_id, data)
                )
            else:
                result = playlist_service.delete_playlist(playlist_id)
                if socketio:
                    socketio.emit('playlist_deleted', {'playlist_id': playlist_id})
                return jsonify(result)
        except Exception as e:
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
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>/reorder', methods=['POST'])
    @login_required
    def reorder_playlist_items(playlist_id):
        data = request.get_json()
        if not data or 'item_id' not in data or 'position' not in data:
            return jsonify({"success": False, "error": "Invalid data"}), 400

        try:
            success = playlist_service.reorder_single_item(
                playlist_id,
                data['item_id'],
                data['position']
            )
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ======================
    # Media File Handling (/api/media)
    # ======================
    @api_bp.route('/media/files', methods=['GET'])
    @login_required
    def get_media_files():
        try:
            files = file_service.get_media_files()
            return jsonify({
                "success": True,
                "files": files,
                "count": len(files)
            })
        except Exception as e:
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

            # Сохраняем файл
            filename = secure_filename("idle_logo.jpg")
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Проверяем сохранение
            if not os.path.exists(file_path):
                return jsonify({"success": False, "error": "Save failed"}), 500

            # Перезапускаем воспроизведение
            playback_service = current_app.config['services']['playback_service']
            if not playback_service.restart_idle_logo():
                return jsonify({
                    "success": False,
                    "error": "Logo updated but playback restart failed"
                }), 500

            return jsonify({
                "success": True,
                "filename": filename,
                "logo_status": playback_service.get_current_logo_status()  # Новый метод
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @api_bp.route('/media/logo_status', methods=['GET'])
    @login_required  # Если требуется авторизация
    def get_logo_status():
        """Получение статуса текущего логотипа"""
        try:
            playback_service = current_app.config['services']['playback_service']
        
            # Получаем путь через защищённый метод
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

    @api_bp.route('/media/<path:filename>', methods=['GET'])
    def serve_media(filename):
        """Serve media files (public endpoint)"""
        try:
            # Получаем UPLOAD_FOLDER из конфига приложения
            upload_folder = current_app.config.get('UPLOAD_FOLDER', '/var/lib/dsign/media')
            
            # Проверяем существование файла перед отправкой
            file_path = os.path.join(upload_folder, filename)
            if not os.path.exists(file_path):
                current_app.logger.warning(f"File not found: {file_path}")
                abort(404, description="Media file not found")
            
            return send_from_directory(
                upload_folder,
                filename,
                mimetype=None,
                as_attachment=False,
                conditional=True
            )
        except KeyError:
            current_app.logger.error("UPLOAD_FOLDER not configured in app config")
            abort(500, description="Server configuration error")
        except Exception as e:
            current_app.logger.error(f"Error serving media file: {str(e)}")
            abort(500, description="Internal server error")
