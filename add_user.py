import sqlite3
from flask_bcrypt import Bcrypt

# Путь к базе данных
DB_PATH = "/var/lib/dsign/database.db"

# Инициализация Bcrypt
bcrypt = Bcrypt()

def add_user(username, password):
    try:
        # Хэширование пароля
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        # Подключение к базе данных
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Вставка пользователя в таблицу
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
        conn.commit()
        conn.close()

        print(f"User '{username}' added successfully.")
    except sqlite3.Error as e:
        print(f"Database error: {e}")

# Пример добавления пользователя
if __name__ == '__main__':
    username = input("Enter username: ")
    password = input("Enter password: ")
    add_user(username, password)