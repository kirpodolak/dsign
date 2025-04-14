import logging
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from typing import List, Dict, Optional
from .extensions import db, bcrypt
import time
from sqlalchemy import event

logger = logging.getLogger(__name__)

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    
    def set_password(self, password):
        """Hash password using bcrypt"""
        self.password = bcrypt.generate_password_hash(password).decode('utf-8')
        self.password_version = 'bcrypt'
    
    def check_password(self, password):
        """Check password using bcrypt"""
        if not self.password:
            return False
            
        try:
            if self.password.startswith(('$2a$', '$2b$')):
                return bcrypt.check_password_hash(self.password, password)
            else:
                return check_password_hash(self.password, password)
        except Exception as e:
            logger.error(f"Password verification failed: {str(e)}")
            return False
    
    def upgrade_password(self, new_password: str, force: bool = False) -> bool:
        if not new_password:
            raise ValueError("Password cannot be empty")

        if not force and self.password.startswith(('$2a$', '$2b$')):
            return False

        try:
            self.set_password(new_password)
            db.session.commit()
            logger.info(f"Password upgraded to bcrypt for user {self.username}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to upgrade password for user {self.username}: {str(e)}")
            raise

class PlaylistFiles(db.Model):
    """Модель файлов в плейлисте"""
    __tablename__ = 'playlist_files'
    __table_args__ = (
        # Уникальный индекс для предотвращения дубликатов позиций
        db.UniqueConstraint('playlist_id', 'order', name='uq_playlist_file_order'),
        # Индекс для ускорения сортировки
        db.Index('ix_playlist_file_order', 'playlist_id', 'order'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlists.id', ondelete='CASCADE'), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    duration = db.Column(db.Integer)
    order = db.Column(db.Integer, nullable=False)  # Теперь обязательное поле
    created_at = db.Column(db.Integer, default=lambda: int(time.time()))
    
    @property
    def order_or_id(self):
        """Возвращает порядок или ID (fallback)"""
        return self.order if self.order is not None else self.id
    
    @property
    def created_dt(self):
        """Возвращает дату создания как datetime"""
        return datetime.fromtimestamp(self.created_at) if self.created_at else None

    @classmethod
    def get_max_order(cls, playlist_id: int) -> int:
        """Получить максимальный порядковый номер в плейлисте"""
        from sqlalchemy import func
        max_order = db.session.query(func.max(cls.order)).filter(
            cls.playlist_id == playlist_id
        ).scalar()
        return max_order if max_order else 0
        
    def validate_order(self):
        """Проверка корректности порядка"""
        if self.order is None:
            raise ValueError("Order cannot be null")
        if self.order < 1:
            raise ValueError("Order must be positive")

    @staticmethod
    def reorder_items(playlist_id: int, new_order: list[int]):
        """
        Массовое обновление позиций элементов
        Args:
            playlist_id: ID плейлиста
            new_order: Список ID элементов в новом порядке
        """
        from sqlalchemy import case
        try:
            # Создаем CASE выражение для массового обновления
            order_case = case(
                {id: index for index, id in enumerate(new_order, 1)},
                value=PlaylistFiles.id
            )
            
            db.session.query(PlaylistFiles).filter(
                PlaylistFiles.playlist_id == playlist_id,
                PlaylistFiles.id.in_(new_order)
            ).update({
                'order': order_case
            }, synchronize_session=False)
            
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            raise RuntimeError(f"Reorder failed: {str(e)}") from e
        
    def __repr__(self) -> str:
        return f'<PlaylistFile {self.id} [{self.order}]: {self.file_name}>'

class Playlist(db.Model):
    """Модель плейлиста"""
    __tablename__ = 'playlists'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    customer = db.Column(db.String(100))
    
    files = db.relationship(
        'PlaylistFiles', 
        backref='playlist', 
        lazy=True, 
        cascade='all, delete-orphan',
        order_by='PlaylistFiles.order'
    )
    
    created_at = db.Column(db.Integer, default=lambda: int(time.time()))
    last_modified = db.Column(db.Integer, default=lambda: int(time.time()))
    
    @property
    def files_count(self):
        """Количество файлов в плейлисте"""
        return len(self.files) if self.files else 0
    
    @property
    def created_dt(self):
        """Дата создания как datetime"""
        return datetime.fromtimestamp(self.created_at) if self.created_at else None
    
    @property
    def modified_dt(self):
        """Дата изменения как datetime"""
        return datetime.fromtimestamp(self.last_modified) if self.last_modified else None
    
    def to_dict(self, include_files: bool = False) -> Dict:
        """Сериализация в словарь"""
        def safe_timestamp(ts):
            if ts is None:
                return None
            return datetime.fromtimestamp(ts).isoformat() if isinstance(ts, (int, float)) else ts
            
        result = {
            'id': self.id,
            'name': self.name,
            'customer': self.customer,
            'created_at': safe_timestamp(self.created_at),
            'last_modified': safe_timestamp(self.last_modified),
            'files_count': self.files_count
        }
        
        if include_files:
            result['files'] = [{
                'id': f.id,
                'file_name': f.file_name,
                'duration': f.duration,
                'order': f.order,
                'created_at': safe_timestamp(f.created_at)
            } for f in sorted(self.files, key=lambda x: x.order_or_id)]
            
        return result

class PlaybackStatus(db.Model):
    """Модель статуса воспроизведения"""
    __tablename__ = 'playback_status'
    
    id = db.Column(db.Integer, primary_key=True, default=1)
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlists.id'))
    status = db.Column(db.String(20), default='stopped')
    timestamp = db.Column(db.Integer, default=lambda: int(time.time()))
    
    @property
    def timestamp_dt(self):
        """Временная метка как datetime"""
        return datetime.fromtimestamp(self.timestamp) if self.timestamp else None
        
    playlist = db.relationship('Playlist', backref='playback_statuses')
    
    def __repr__(self) -> str:
        return f'<PlaybackStatus {self.status}>'
        
    def to_dict(self) -> Dict:
        return {
            'status': self.status,
            'playlist_id': self.playlist_id,
            'timestamp': self.timestamp_dt.isoformat() if self.timestamp_dt else None
        }

class PlaybackProfile(db.Model):
    """Профиль воспроизведения"""
    __tablename__ = 'playback_profiles'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    profile_type = db.Column(db.String(20), nullable=False)  # 'idle' или 'playlist'
    settings = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.Integer, default=lambda: int(time.time()))
    
    @property
    def created_dt(self):
        """Дата создания как datetime"""
        return datetime.fromtimestamp(self.created_at) if self.created_at else None

class PlaylistProfileAssignment(db.Model):
    """Связь плейлиста с профилем воспроизведения"""
    __tablename__ = 'playlist_profile_assignments'
    
    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlists.id'), unique=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('playback_profiles.id'))
    assigned_at = db.Column(db.Integer, default=lambda: int(time.time()))
    
    @property
    def assigned_dt(self):
        """Дата назначения как datetime"""
        return datetime.fromtimestamp(self.assigned_at) if self.assigned_at else None

# Автоматическое обновление временных меток
@event.listens_for(Playlist, 'before_update')
def update_modified_timestamp(mapper, connection, target):
    target.last_modified = int(time.time())
