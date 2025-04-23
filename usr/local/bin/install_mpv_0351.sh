#!/bin/bash
set -e

# === Настройки ===
MPV_VERSION="v0.35.1"  # или v0.38.0 (если у тебя FFmpeg >=6.0)
MPV_DIR="$HOME/mpv"
MPV_SRC="$MPV_DIR/mpv"
BUILD_DIR="$MPV_SRC/build"

# === Подготовка окружения ===
echo "[1/7] Установка зависимостей..."
sudo apt update
sudo apt install -y git python3-pip ninja-build \
    build-essential pkg-config libtool liblua5.2-dev \
    libavutil-dev libavcodec-dev libavformat-dev libswresample-dev \
    libavfilter-dev libjpeg-dev libass-dev libfreetype6-dev \
    libdrm-dev libegl1-mesa-dev libgbm-dev libwayland-dev libx11-dev \
    libxext-dev libxrandr-dev libxinerama-dev libgl1-mesa-dev

echo "[2/7] Установка Meson (если устарел)..."
pip3 install --user --upgrade meson

export PATH="$HOME/.local/bin:$PATH"

echo "[3/7] Клонирование mpv ($MPV_VERSION)..."
mkdir -p "$MPV_DIR"
cd "$MPV_DIR"
rm -rf mpv
git clone --depth 1 --branch "$MPV_VERSION" https://github.com/mpv-player/mpv.git
cd "$MPV_SRC"

echo "[4/7] Очистка предыдущей сборки..."
rm -rf "$BUILD_DIR"

echo "[5/7] Настройка сборки через Meson..."
meson setup "$BUILD_DIR" --buildtype=release -Dlibmpv=true -Dlua=enabled

echo "[6/7] Сборка mpv..."
ninja -C "$BUILD_DIR"

echo "[7/7] Установка mpv..."
meson install -C "$BUILD_DIR"

echo "✅ mpv $MPV_VERSION успешно установлен!"
