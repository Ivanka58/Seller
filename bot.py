import os
import telebot
from telebot import types
from flask import Flask
import threading
from dotenv import load_dotenv

load_dotenv()

# Настройки из ENV
TOKEN = os.getenv("TG_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID") # ID канала, куда слать посты

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Временное хранилище данных пользователя
user_data = {}

# --- СЕРВЕР ДЛЯ ПОРТА RENDER ---
@app.route('/')
def health():
    return "Bot is alive", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- КЛАВИАТУРЫ ---
def get_start_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("Отправить объявление"))
    return kb

def get_confirm_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("Готово ☑️"), types.KeyboardButton("Изменить"))
    return kb

# --- КОМАНДЫ ---
@bot.message_handler(commands=['start', 'auto'])
def send_welcome(message):
    user_id = message.chat.id
    user_data[user_id] = {} # Очистка данных
    bot.send_message(
        user_id, 
        "Чтобы отправить объявление нажмите ниже", 
        reply_markup=get_start_kb()
    )

# Кнопка "Отправить объявление"
@bot.message_handler(func=lambda m: m.text == "Отправить объявление")
def ask_photo(message):
    bot.send_message(message.chat.id, "Отправьте фотографию вашего объявления", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, get_photo)

# Шаг 1: Получаем фото
def get_photo(message):
    if not message.photo:
        bot.send_message(message.chat.id, "Это не фотография! Нажми /start и начни заново.")
        return
    
    # Сохраняем file_id лучшего качества
    user_data[message.chat.id]['photo_id'] = message.photo[-1].file_id
    
    bot.send_message(message.chat.id, "Теперь отправьте текст к вашему фото")
    bot.register_next_step_handler(message, get_text)

# Шаг 2: Получаем текст
def get_text(message):
    if not message.text:
        bot.send_message(message.chat.id, "Нужен текст! Нажми /start и начни заново.")
        return
    
    user_data[message.chat.id]['text'] = message.text
    
    bot.send_message(
        message.chat.id, 
        "Объявление готово к публикации, вы уверены? Если нужно что-то изменить нажмите ниже", 
        reply_markup=get_confirm_kb()
    )

# Шаг 3: Подтверждение
@bot.message_handler(func=lambda m: m.text in ["Готово ☑️", "Изменить"])

def confirm_step(message):
    chat_id = message.chat.id
    
    if message.text == "Изменить":
        # Начинаем процесс заново
        user_data[chat_id] = {}
        ask_photo(message)
        return

    # Если нажали "Готово ☑️"
    temp_msg = bot.send_message(chat_id, "Объявление публикуется", reply_markup=get_start_kb())
    
    try:
        data = user_data.get(chat_id)
        if not data or 'photo_id' not in data:
            raise Exception("Данные потеряны")

        # ПУБЛИКАЦИЯ В КАНАЛ
        bot.send_photo(
            chat_id=CHANNEL_ID, 
            photo=data['photo_id'], 
            caption=data['text']
        )

        # Удаляем сообщение "публикуется" и шлем успех
        bot.delete_message(chat_id, temp_msg.message_id)
        bot.send_message(chat_id, "Объявление опубликовано")
        
    except telebot.apihelper.ApiTelegramException as e:
        # Ошибки прав доступа (если канал закрыт или бот не админ)
        if "chat not found" in str(e).lower() or "forbidden" in str(e).lower():
            bot.send_message(chat_id, "Ошибка, группа закрыта, обратитесь к администратору @Ivanka58")
        else:
            bot.send_message(chat_id, "Ошибка, обратитесь к администратору @Ivanka58")
        print(f"Telegram Error: {e}")
        
    except Exception as e:
        # Любые другие ошибки
        bot.send_message(chat_id, "Критическая ошибка, обратитесь к администратору @Ivanka58")
        print(f"General Error: {e}")

# --- ЗАПУСК ---
if __name__ == '__main__':
    # Поток для порта
    threading.Thread(target=run_flask, daemon=True).start()
    print("Бот для канала запущен...")
    bot.infinity_polling()
