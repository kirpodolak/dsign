# В начале файла:
from dsign.services.logger import setup_logger
logger = setup_logger('LogAPI')

@api.route('/api/logs', methods=['POST'])
def log_client_event():
    try:
        log_data = request.get_json()
        logger.log(log_data.get('level', 'info'), 'Client log', {
            'module': log_data.get('module'),
            'message': log_data.get('message'),
            'client_data': log_data.get('data')
        })
        return jsonify({'status': 'logged'})
    except Exception as e:
        logger.error('Failed to log client event', {
            'error': str(e),
            'original_data': request.get_json()
        })
        return jsonify({'error': str(e)}), 500
