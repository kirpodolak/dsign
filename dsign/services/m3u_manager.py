# m3u_manager.py
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

class M3UManager:
    def __init__(self, logger, media_root, upload_folder):
        self.logger = logger
        self.media_root = Path(media_root)
        self.upload_folder = Path(upload_folder)
        self.tmp_dir = self.upload_folder / 'tmp'
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
    
    def ensure_proper_m3u_format(self, playlist_file: Path) -> str:
        """Обеспечивает правильный формат M3U для слайд-шоу"""
        try:
            with open(playlist_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.logger.info(f"Проверка формата M3U файла: {playlist_file}")
            
            # Если файл уже в правильном формате, возвращаем как есть
            if '#DSIGN-PLAYLIST-VERSION:2' in content:
                self.logger.info("M3U файл уже в правильном формате слайд-шоу")
                return str(playlist_file.absolute())
            
            # Если это стандартный M3U, конвертируем в наш формат
            elif content.startswith('#EXTM3U'):
                self.logger.info("Конвертация стандартного M3U в формат слайд-шоу")
                return self._convert_standard_m3u_to_slideshow(playlist_file, content)
            
            # Если это простой список файлов, создаем полный формат
            else:
                self.logger.info("Создание формата слайд-шоу из списка файлов")
                return self._create_slideshow_format_from_list(playlist_file, content)
                
        except Exception as e:
            self.logger.error(f"Ошибка проверки формата M3U: {str(e)}")
            return str(playlist_file.absolute())
    
    def _convert_standard_m3u_to_slideshow(self, original_file: Path, content: str) -> str:
        """Конвертирует стандартный M3U в формат для слайд-шоу"""
        try:
            lines = content.split('\n')
            output_lines = ["#EXTM3U", "#DSIGN-PLAYLIST-VERSION:2"]
            
            in_extinf = False
            current_duration = 5
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Обработка EXTINF
                if line.startswith('#EXTINF:'):
                    in_extinf = True
                    # Извлекаем длительность из EXTINF
                    match = re.match(r'#EXTINF:(\d+)', line)
                    if match:
                        current_duration = int(match.group(1))
                    continue
                
                # Если строка после EXTINF - это файл
                if in_extinf and not line.startswith('#'):
                    # Определяем тип файла
                    file_ext = line.lower().split('.')[-1] if '.' in line else ''
                    
                    if file_ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']:
                        output_lines.append(f"#TYPE:IMAGE:DURATION:{current_duration}")
                    elif file_ext in ['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv']:
                        output_lines.append("#TYPE:VIDEO")
                    else:
                        output_lines.append(f"#TYPE:UNKNOWN:DURATION:{current_duration}")
                    
                    # Очищаем путь и добавляем
                    cleaned_path = self._clean_file_path(line)
                    resolved_path = self._resolve_file_path(cleaned_path)
                    output_lines.append(resolved_path)
                    
                    in_extinf = False
                    current_duration = 5  # Сброс к значению по умолчанию
                    
                # Пропускаем другие комментарии
                elif line.startswith('#'):
                    continue
                    
                # Просто путь к файлу без EXTINF
                else:
                    file_ext = line.lower().split('.')[-1] if '.' in line else ''
                    
                    if file_ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']:
                        output_lines.append(f"#TYPE:IMAGE:DURATION:5")
                    elif file_ext in ['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv']:
                        output_lines.append("#TYPE:VIDEO")
                    else:
                        output_lines.append("#TYPE:UNKNOWN")
                    
                    # Очищаем путь и добавляем
                    cleaned_path = self._clean_file_path(line)
                    resolved_path = self._resolve_file_path(cleaned_path)
                    output_lines.append(resolved_path)
            
            # Сохраняем конвертированный файл
            temp_file = self.tmp_dir / f"slideshow_{original_file.name}"
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(output_lines))
            
            self.logger.info(f"Конвертированный M3U файл: {temp_file}")
            return str(temp_file.absolute())
            
        except Exception as e:
            self.logger.error(f"Ошибка конвертации M3U: {str(e)}")
            return str(original_file.absolute())
    
    def _create_slideshow_format_from_list(self, original_file: Path, content: str) -> str:
        """Создает формат слайд-шоу из простого списка файлов"""
        try:
            lines = content.split('\n')
            output_lines = ["#EXTM3U", "#DSIGN-PLAYLIST-VERSION:2"]
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Определяем тип файла
                file_ext = line.lower().split('.')[-1] if '.' in line else ''
                
                if file_ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']:
                    output_lines.append(f"#TYPE:IMAGE:DURATION:5")
                elif file_ext in ['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv']:
                    output_lines.append("#TYPE:VIDEO")
                else:
                    output_lines.append("#TYPE:UNKNOWN")
                
                # Очищаем путь и добавляем
                cleaned_path = self._clean_file_path(line)
                resolved_path = self._resolve_file_path(cleaned_path)
                output_lines.append(resolved_path)
            
            # Сохраняем файл
            temp_file = self.tmp_dir / f"slideshow_{original_file.name}"
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(output_lines))
            
            self.logger.info(f"Создан M3U файл слайд-шоу: {temp_file}")
            return str(temp_file.absolute())
            
        except Exception as e:
            self.logger.error(f"Ошибка создания формата слайд-шоу: {str(e)}")
            return str(original_file.absolute())
    
    def _resolve_file_path(self, file_path: str) -> str:
        """Разрешает путь к файлу в абсолютный"""
        # Если путь уже абсолютный, возвращаем как есть
        if os.path.isabs(file_path):
            return file_path
        
        # Проверяем в медиа директории
        media_path = self.media_root / file_path
        if media_path.exists():
            return str(media_path.absolute())
        
        # Проверяем в upload директории
        upload_path = self.upload_folder / file_path
        if upload_path.exists():
            return str(upload_path.absolute())
        
        # Если файл не найден, возвращаем оригинальный путь
        self.logger.warning(f"Файл не найден: {file_path}")
        return file_path
    
    def _clean_file_path(self, file_path: str) -> str:
        """Очищает путь от параметров запроса и нормализует"""
        # Удаляем параметры запроса (все что после ?)
        if '?' in file_path:
            file_path = file_path.split('?')[0]
        
        # Удаляем якоря (все что после #)
        if '#' in file_path:
            file_path = file_path.split('#')[0]
            
        return file_path.strip()
