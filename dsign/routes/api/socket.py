from flask import request, current_app
from flask_socketio import SocketIO, disconnect

socketio = SocketIO()

def init_socketio(app):
    socketio.init_app(app)
    return socketio

@socketio.on('connect')
def handle_connect():
    try:
        token = request.args.get('token')
        if not token:
            raise ValueError('No token provided')
            
        auth_data = current_app.socket_service.verify_socket_token(token)
        current_app.logger.info(f"User {auth_data['user_id']} connected via WebSocket")
        
    except Exception as e:
        current_app.logger.warning(f"WebSocket connection rejected: {str(e)}")
        disconnect()
