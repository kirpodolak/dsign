from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, SubmitField, SelectField,
    MultipleFileField, FileField
)
from wtforms.validators import InputRequired, Length
from flask_wtf.file import FileAllowed
from wtforms import BooleanField


class LoginForm(FlaskForm):
    """Форма входа в систему"""
    username = StringField('Username', validators=[
        InputRequired(), Length(min=3, max=50)
    ])
    password = PasswordField('Password', validators=[
        InputRequired(), Length(min=3, max=100)
    ])
    remember = BooleanField('Remember Me')  # ✅ Для совместимости с фронтом
    submit = SubmitField('Login')


class UploadForm(FlaskForm):
    """Форма загрузки медиафайлов"""
    def __init__(self, allowed_extensions, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.files.validators.append(FileAllowed(
            allowed_extensions, message="Only images and videos allowed!"
        ))

    files = MultipleFileField('Upload Files', validators=[
        InputRequired(message="Please select at least one file")
    ])
    submit = SubmitField('Upload')


class UploadLogoForm(FlaskForm):
    """Форма загрузки логотипа"""
    def __init__(self, allowed_logo_extensions, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logo.validators.append(FileAllowed(
            allowed_logo_extensions, message="Only image files allowed!"
        ))

    logo = FileField('Upload Logo', validators=[
        InputRequired(message="Please select a logo image file")
    ])
    submit = SubmitField('Update Logo')


class SettingsForm(FlaskForm):
    """
    Форма для настроек MPV — формируется динамически.
    Для каждой опции создаётся поле вручную при инициализации.
    """
    def __init__(self, dynamic_fields: dict, *args, **kwargs):
        """
        :param dynamic_fields: словарь вида {'category.option': {'label': str, 'choices': list[str], 'default': str}}
        """
        super().__init__(*args, **kwargs)

        for field_key, meta in dynamic_fields.items():
            label = meta.get('label', field_key)
            choices = meta.get('choices', [])
            default = meta.get('default', '')

            field = SelectField(
                label=label,
                choices=[(c, c) for c in choices],
                default=default
            )
            setattr(self, field_key, field)

    submit = SubmitField('Save Settings')


class PlaylistProfileForm(FlaskForm):
    """Форма назначения MPV-профиля плейлисту"""
    playlist_id = StringField('Playlist ID', validators=[InputRequired()])
    profile_name = SelectField('Select Profile', choices=[], validators=[InputRequired()])
    submit = SubmitField('Apply Profile')
