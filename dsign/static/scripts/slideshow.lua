-- DSign Slideshow Script v3.0
-- Улучшенное слайд-шоу для MPV с автоопределением типов файлов

local slide_durations = {}  -- Длительности для каждого слайда
local file_types = {}       -- Типы файлов (image/video)
local playlist_entries = {} -- Записи плейлиста для быстрого доступа
local current_slide = 1
local slideshow_timer = nil
local is_paused = false
local last_playlist_update = 0
local PLAYLIST_UPDATE_INTERVAL = 30 -- Обновлять данные из M3U каждые 30 секунд

-- Определение типа файла по расширению
function detect_file_type_by_extension(filename)
    if not filename then return "unknown", 5 end
    
    local ext = filename:match("%.([^%.]+)$")
    if not ext then return "unknown", 5 end
    
    ext = ext:lower()
    
    -- Расширения изображений
    local image_exts = {
        jpg = true, jpeg = true, png = true,
        gif = true, bmp = true, tiff = true,
        webp = true, svg = true, jfif = true,
        pnm = true, ppm = true, pgm = true,
        pbm = true
    }
    
    -- Расширения видео
    local video_exts = {
        mp4 = true, mkv = true, avi = true,
        mov = true, webm = true, flv = true,
        wmv = true, m4v = true, mpg = true,
        mpeg = true, m2ts = true, ts = true,
        mts = true, vob = true, ogv = true,
        rm = true, rmvb = true, asf = true,
        mxf = true, m4a = true, "3gp" = true
    }
    
    if image_exts[ext] then
        return "image", 5 -- 5 секунд по умолчанию для изображений
    elseif video_exts[ext] then
        return "video", -1 -- -1 = полная длительность видео
    end
    
    return "unknown", 5 -- По умолчанию 5 секунд
end

-- Получение пути к текущему плейлисту
function get_current_playlist_path()
    -- Пробуем несколько способов получить путь к плейлисту
    local paths_to_try = {
        mp.get_property("playlist-path"),
        mp.get_property("playlist"),
        mp.get_property("path"), -- Текущий файл может быть плейлистом
        mp.get_property("stream-open-filename")
    }
    
    for _, path in ipairs(paths_to_try) do
        if path and path ~= "" and not path:find("^%w+://") then
            -- Проверяем, является ли это плейлистом
            if path:match("%.m3u8?$") then
                return path
            end
        end
    end
    
    return nil
end

-- Чтение и парсинг M3U файла с автоопределением типов
function parse_playlist_with_autodetect(playlist_path)
    local durations = {}
    local types = {}
    local entries = {}
    local slide_index = 1
    local has_metadata = false
    
    if not playlist_path then
        mp.msg.warn("Не указан путь к плейлисту для парсинга")
        return durations, types, entries, false
    end
    
    local file, err = io.open(playlist_path, "r")
    if not file then
        mp.msg.error("Не удалось открыть плейлист: " .. (err or "неизвестная ошибка"))
        return durations, types, entries, false
    end
    
    local lines = {}
    for line in file:lines() do
        table.insert(lines, line)
    end
    file:close()
    
    -- Проверяем, является ли файл M3U
    if #lines > 0 and lines[1]:match("^#EXTM3U") then
        mp.msg.verbose("Обнаружен M3U плейлист с метаданными")
        has_metadata = true
    end
    
    for i = 1, #lines do
        local line = lines[i]:trim()
        
        if line:startswith("#TYPE:IMAGE:DURATION:") then
            -- Извлекаем длительность из метаданных M3U
            local dur_str = line:match("DURATION:(%d+)")
            local duration = tonumber(dur_str) or 5
            durations[slide_index] = duration
            types[slide_index] = "image"
            mp.msg.verbose("Метаданные M3U: слайд " .. slide_index .. " - изображение, " .. duration .. " сек")
            
        elseif line:startswith("#TYPE:VIDEO") then
            -- Видео из метаданных M3U
            durations[slide_index] = -1
            types[slide_index] = "video"
            mp.msg.verbose("Метаданные M3U: слайд " .. slide_index .. " - видео, полная длительность")
            
        elseif line:startswith("#EXTINF:") then
            -- Стандартный формат M3U (#EXTINF:длительность,название)
            local dur_str = line:match("^#EXTINF:(%d+)")
            if dur_str then
                local duration = tonumber(dur_str)
                if duration and duration > 0 then
                    durations[slide_index] = duration
                    mp.msg.verbose("EXTINF: слайд " .. slide_index .. " - " .. duration .. " сек")
                end
            end
            
        elseif not line:startswith("#") and line ~= "" then
            -- Это путь к файлу
            entries[slide_index] = line
            
            -- Определяем тип файла по расширению если нет в метаданных
            if not types[slide_index] then
                local file_type, default_duration = detect_file_type_by_extension(line)
                types[slide_index] = file_type
                
                -- Устанавливаем длительность если не задана в метаданных
                if not durations[slide_index] then
                    durations[slide_index] = default_duration
                    mp.msg.verbose("Автоопределение: слайд " .. slide_index .. " - " .. 
                                  file_type .. ", " .. default_duration .. " сек")
                end
            end
            
            slide_index = slide_index + 1
        end
    end
    
    mp.msg.info("Загружен плейлист: " .. (slide_index - 1) .. " слайдов, " .. 
                (has_metadata and "с метаданными" or "без метаданных"))
    
    return durations, types, entries, has_metadata
end

-- Обновление данных плейлиста (можно вызывать периодически)
function update_playlist_data()
    local playlist_path = get_current_playlist_path()
    if not playlist_path then
        mp.msg.warn("Не удалось определить путь к плейлисту для обновления")
        return false
    end
    
    local new_durations, new_types, new_entries, has_metadata = 
        parse_playlist_with_autodetect(playlist_path)
    
    if #new_durations > 0 then
        slide_durations = new_durations
        file_types = new_types
        playlist_entries = new_entries
        last_playlist_update = os.time()
        
        mp.msg.verbose("Данные плейлиста обновлены, слайдов: " .. #new_durations)
        return true
    end
    
    return false
end

-- Получение длительности текущего слайда
function get_current_slide_duration()
    local duration = slide_durations[current_slide]
    
    if duration then
        return duration
    end
    
    -- Если нет данных, пробуем определить по текущему файлу
    local current_file = mp.get_property("path")
    if current_file then
        local file_type, default_duration = detect_file_type_by_extension(current_file)
        return default_duration
    end
    
    return 5 -- Значение по умолчанию
end

-- Получение типа текущего слайда
function get_current_slide_type()
    local slide_type = file_types[current_slide]
    
    if slide_type then
        return slide_type
    end
    
    -- Если нет данных, пробуем определить по текущему файлу
    local current_file = mp.get_property("path")
    if current_file then
        local file_type = detect_file_type_by_extension(current_file)
        return file_type
    end
    
    return "unknown"
end

-- Запуск таймера для текущего слайда с учетом типа
function start_slide_timer()
    if slideshow_timer then
        slideshow_timer:kill()
        slideshow_timer = nil
    end
    
    -- Проверяем, нужно ли обновить данные плейлиста
    local now = os.time()
    if now - last_playlist_update > PLAYLIST_UPDATE_INTERVAL then
        mp.msg.verbose("Плановое обновление данных плейлиста")
        update_playlist_data()
    end
    
    local slide_type = get_current_slide_type()
    local duration = get_current_slide_duration()
    
    if slide_type == "image" then
        -- Для изображений: запускаем таймер
        if duration and duration > 0 then
            mp.msg.verbose("Изображение: таймер на " .. duration .. " сек")
            slideshow_timer = mp.add_timeout(duration, next_slide)
        else
            mp.msg.warn("Некорректная длительность для изображения, используем 5 сек")
            slideshow_timer = mp.add_timeout(5, next_slide)
        end
        
    elseif slide_type == "video" then
        -- Для видео: проверяем текущую длительность
        if duration == -1 then
            mp.msg.verbose("Видео: играет полную длительность")
            -- Не запускаем таймер, ждем окончания видео
        else
            -- Если указана конкретная длительность
            mp.msg.verbose("Видео: ограниченная длительность " .. duration .. " сек")
            slideshow_timer = mp.add_timeout(duration, next_slide)
        end
        
    else
        -- Неизвестный тип: используем таймер
        local fallback_duration = duration or 5
        mp.msg.warn("Неизвестный тип файла, используем таймер на " .. fallback_duration .. " сек")
        slideshow_timer = mp.add_timeout(fallback_duration, next_slide)
    end
end

-- Переход к следующему слайду
function next_slide()
    if is_paused then
        mp.msg.verbose("Слайд-шоу на паузе, пропускаем переход")
        return
    end
    
    -- Получаем количество слайдов
    local slide_count = math.max(#slide_durations, mp.get_property_number("playlist-count", 0))
    if slide_count == 0 then
        mp.msg.warn("Плейлист пуст, нечего переключать")
        return
    end
    
    mp.msg.verbose("Переход к следующему слайду (сейчас: " .. current_slide .. " из " .. slide_count .. ")")
    
    -- Переходим к следующему слайду
    mp.commandv("playlist-next", "weak")
    
    -- Обновляем счетчик
    current_slide = current_slide + 1
    if current_slide > slide_count then
        current_slide = 1  -- Зацикливание
        mp.msg.verbose("Начало нового цикла слайд-шоу")
    end
    
    -- Запускаем таймер для нового слайда
    start_slide_timer()
end

-- Событие: плейлист загружен
mp.register_event("playlist-loaded", function()
    mp.msg.info("Плейлист загружен в MPV")
    
    -- Пробуем получить и парсить плейлист
    local success = update_playlist_data()
    
    if success and #slide_durations > 0 then
        current_slide = 1
        mp.msg.info("Слайд-шоу готово: " .. #slide_durations .. " слайдов")
        
        -- Автоматически снимаем с паузы при загрузке плейлиста
        local paused = mp.get_property("pause", false)
        if paused then
            mp.set_property("pause", "no")
            mp.msg.info("Автоматически сняли с паузы")
        end
        
        -- Запускаем таймер если не на паузе
        if not is_paused then
            start_slide_timer()
        end
    else
        mp.msg.warn("Не удалось загрузить данные плейлиста, будет использовано автоопределение")
    end
end)

-- Событие: файл начал воспроизводиться
mp.register_event("file-loaded", function()
    mp.msg.verbose("Файл загружен")
    
    -- Автоматически снимаем с паузы при загрузке файла
    local paused = mp.get_property("pause", false)
    if paused then
        mp.set_property("pause", "no")
        mp.msg.verbose("Автоматически сняли с паузы")
    end
    
    -- Если это первый файл и данные плейлиста еще не загружены
    if #slide_durations == 0 then
        update_playlist_data()
    end
    
    -- Обновляем текущую позицию
    local pos = mp.get_property_number("playlist-pos", -1)
    if pos >= 0 then
        current_slide = pos + 1
    end
    
    -- Запускаем таймер если не на паузе
    if not is_paused then
        start_slide_timer()
    end
end)

-- Событие: окончание файла
mp.register_event("end-file", function(event)
    if event.reason == "eof" then
        mp.msg.verbose("Файл завершен (EOF), переходим к следующему")
        next_slide()
    end
end)

-- Обработка паузы
mp.observe_property("pause", "bool", function(name, value)
    local was_paused = is_paused
    is_paused = value
    
    mp.msg.verbose("Пауза: " .. tostring(value))
    
    if value and slideshow_timer then
        -- Пауза: останавливаем таймер
        slideshow_timer:kill()
        slideshow_timer = nil
        mp.msg.verbose("Слайд-шоу приостановлено")
    elseif not value and not slideshow_timer then
        -- Снятие паузы: перезапускаем таймер
        mp.msg.verbose("Слайд-шоу возобновлено")
        start_slide_timer()
    end
end)

-- Обработка ручного переключения слайдов
mp.observe_property("playlist-pos", "number", function(name, value)
    if value and value >= 0 then
        local new_slide = value + 1  -- MPV индексы с 0, Lua с 1
        
        if new_slide ~= current_slide then
            current_slide = new_slide
            mp.msg.verbose("Ручной переход к слайду " .. current_slide)
            
            if not is_paused then
                start_slide_timer()
            end
        end
    end
end)

-- Команда для принудительного обновления плейлиста
mp.register_script_message("update-playlist", function()
    mp.msg.info("Принудительное обновление данных плейлиста")
    if update_playlist_data() then
        mp.msg.info("Данные плейлиста успешно обновлены")
    else
        mp.msg.error("Не удалось обновить данные плейлиста")
    end
end)

-- Команда для получения информации о текущем слайде
mp.register_script_message("current-slide-info", function()
    local slide_type = get_current_slide_type()
    local duration = get_current_slide_duration()
    local total_slides = math.max(#slide_durations, mp.get_property_number("playlist-count", 0))
    
    mp.msg.info("Текущий слайд: " .. current_slide .. "/" .. total_slides .. 
                ", тип: " .. slide_type .. ", длительность: " .. 
                (duration == -1 and "полная" or duration .. " сек"))
end)

-- Инициализация
mp.register_event("start-file", function()
    mp.msg.info("DSign Slideshow Script v3.0 инициализирован")
    mp.msg.info("Автоопределение типов файлов включено")
    mp.msg.info("Автообновление плейлиста каждые " .. PLAYLIST_UPDATE_INTERVAL .. " секунд")
end)
