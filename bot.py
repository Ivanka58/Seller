import os
import telebot
from telebot import types
from flask import Flask
import threading
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("TG_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # ID канала
GROUP_ID = os.getenv("GROUP_ID")      # ID группы сотрудников

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- ХРАНИЛИЩЕ ДАННЫХ ---
user_data = {}
user_limits = {}
global_msg_count = 0  # Общий счётчик сообщений в канале
warnings_db = {}  # Словарь для хранения предупреждений

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

def get_finish_photos_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("Закончить отправку фото ✅"))
    return kb

def get_confirm_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("Готово ☑️"), types.KeyboardButton("Изменить"))
    return kb

# --- МОНИТОРИНГ КАНАЛА ---
@bot.channel_post_handler()
def listen_channel(message):
    global global_msg_count
    if str(message.chat.id) == str(CHANNEL_ID):
        global_msg_count += 1
        print(f"Счётчик канала увеличен: {global_msg_count}")

# --- ФУНКЦИИ ПРОВЕРКИ ЛИМИТА ---
def is_user_limited(user_id):
    if user_id not in user_limits:
        return False, 0
    needed_count = user_limits[user_id]
    if global_msg_count < needed_count:
        remaining = needed_count - global_msg_count
        return True, remaining
    return False, 0

# --- ФУНКЦИОНАЛЬНОСТЬ БЛОИРОВКИ И ПРЕДУПРЕЖДЕНИЙ ---
def block_user(chat_id):
    warnings_db[chat_id] = 3  # Максимальное количество предупреждений
    bot.send_message(GROUP_ID, f"Пользователь @{bot.get_chat(chat_id).username} заблокирован.")
    bot.send_message(int(chat_id), "Вы заблокированы администрацией и больше не сможете пользоваться ботом.")

def warn_user(chat_id):
    current_warnings = warnings_db.get(chat_id, 0)
    new_warnings = current_warnings + 1
    warnings_db[chat_id] = new_warnings
    warning_level = f"{new_warnings}/3"
    bot.send_message(GROUP_ID, f"Пользователю @{bot.get_chat(chat_id).username} выдано предупреждение {warning_level}.")
    bot.send_message(int(chat_id), f"Вам выдано предупреждение {warning_level} администрацией.")
    if new_warnings >= 3:
        block_user(chat_id)

# --- ОБРАБОТКА КНОПОК ---
@bot.callback_query_handler(func=lambda call: True)
def button_actions(call):
    chat_id = call.data.split('_')[1]
    action = call.data.split('_')[0]
    if action == "block":
        block_user(chat_id)
    elif action == "warn":
        warn_user(chat_id)

# --- КОМАНДЫ ---
@bot.message_handler(commands=['start', 'auto'])
def send_welcome(message):
    chat_id = message.chat.id
    user_data[chat_id] = {'photos': [], 'text': None}
    bot.send_message(
        chat_id,
        "Привет! Чтобы отправить объявление, нажмите на кнопку ниже 👇",
        reply_markup=get_start_kb()
    )

@bot.message_handler(func=lambda m: m.text == "Отправить объявление")
def ask_photo(message):
    chat_id = message.chat.id
    limited, remaining = is_user_limited(chat_id)
    if limited:
        bot.send_message(
            chat_id,
            f"Вы пока не можете отправить объявление.\n\nНужно, чтобы в канале появилось еще **{remaining}** сообщения.",
            parse_mode="Markdown"
        )
        return
    user_data[chat_id] = {'photos': [], 'text': None}
    bot.send_message(
        chat_id,
        "Отправьте фотографию(ии) вашего объявления",
        reply_markup=types.ReplyKeyboardRemove()
    )

# Прием фотографий
@bot.message_handler(content_types=['photo'])
def handle_photos(message):
    chat_id = message.chat.id
    if chat_id not in user_data: return
    if len(user_data[chat_id]['photos']) < 10:
        file_id = message.photo[-1].file_id
        user_data[chat_id]['photos'].append(file_id)
        bot.send_message(
            chat_id,
            f"Фото получено ({len(user_data[chat_id]['photos'])}/10).\n\nХотите добавить ещё фото или завершить?",
            reply_markup=get_finish_photos_kb()
        )

# Завершение отправки фото
@bot.message_handler(func=lambda m: m.text == "Закончить отправку фото ✅")
def finish_photos_step(message):
    chat_id = message.chat.id
    if chat_id not in user_data or not user_data[chat_id]['photos']:
        bot.send_message(chat_id, "Вы не отправили ни одного фото!")
        return
    bot.send_message(chat_id, "Теперь отправьте текст к вашему объявлению", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, get_text)

# Получение текста объявления
def get_text(message):
    chat_id = message.chat.id
    if not message.text:
        bot.send_message(chat_id, "Пожалуйста, отправьте именно текст.")
        bot.register_next_step_handler(message, get_text)
        return
    user_data[chat_id]['text'] = message.text
    bot.send_message(
        chat_id,
        "Объявление готово к публикации, вы уверены?\n\nВыберите действие ниже:",
        reply_markup=get_confirm_kb()
    )

# Подтверждение публикации
@bot.message_handler(func=lambda m: m.text in ["Готово ☑️", "Изменить"])
def confirm_step(message):
    chat_id = message.chat.id
    if chat_id not in user_data: return
    if message.text == "Изменить":
        user_data[chat_id] = {'photos': [], 'text': None}
        ask_photo(message)
        return
    try:
        data = user_data[chat_id]
        photos = data['photos']
        caption = data['text']
        media = []
        for i, p_id in enumerate(photos):
            if i == 0:
                media.append(types.InputMediaPhoto(p_id, caption=caption))
            else:
                media.append(types.InputMediaPhoto(p_id))
        bot.send_media_group(CHANNEL_ID, media)
        user_limits[chat_id] = global_msg_count + 4
        bot.send_message(chat_id, "Объявление опубликовано!\n\nВы сможете отправить следующее через 3 сообщения в канале.")
    except Exception as e:
        bot.send_message(chat_id, "Ошибка при публикации объявления, попробуйте позже.")
        print(f"Error: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    print("Бот запущен и мониторит канал...")
    bot.infinity_polling()
