#!/usr/bin/env python
import os
from flask_script import Manager
from flask_migrate import Migrate, MigrateCommand
from app import create_app, db

app = create_app(os.getenv('DSIGN_CONFIG') or '/etc/dsign/config.py')
migrate = Migrate(app, db)
manager = Manager(app)

manager.add_command('db', MigrateCommand)

@manager.command
def cleanup():
    """Очистка устаревших файлов"""
    from datetime import datetime, timedelta
    from app.models import File
    from config import Config
    
    threshold = datetime.utcnow() - timedelta(days=30)
    old_files = File.query.filter(File.uploaded_at < threshold).all()
    
    for file in old_files:
        try:
            file_path = os.path.join(Config.UPLOAD_FOLDER, file.filename)
            if os.path.exists(file_path):
                os.unlink(file_path)
            db.session.delete(file)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Ошибка при удалении файла {file.id}: {str(e)}")

if __name__ == '__main__':
    manager.run()
