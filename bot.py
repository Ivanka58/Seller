import os
import telebot
from telebot import types
from flask import Flask
import threading
from dotenv import load_dotenv
import logging

# Настраиваем логгер
logger = telebot.logger
telebot.logger.setLevel(logging.DEBUG)  # Устанавливаем уровень DEBUG для детального вывода

# Создаем обработчик логов, записывающий сообщения в файл
handler = logging.FileHandler('bot.log', mode='w', encoding='utf-8')
formatter = logging.Formatter('%(asctime)s %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

load_dotenv()

TOKEN = os.getenv("TG_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # Убедись, что это ID канала (начинается с -100)
GROUP_ID = os.getenv("GROUP_ID")       # Идентификатор группы сотрудников (добавлена новая переменная)

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- ХРАНИЛИЩЕ ДАННЫХ ---
user_data = {}
user_limits = {}
warnings_db = {}
global_msg_count = 0

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
        logger.info(f"Channel post detected, counter increased to {global_msg_count}")

# --- ФУНКЦИИ ПРОВЕРКИ ЛИМИТА ---
def is_user_limited(user_id):
    if user_id not in user_limits:
        return False, 0
    needed_count = user_limits[user_id]
    if global_msg_count < needed_count:
        remaining = needed_count - global_msg_count
        return True, remaining
    return False, 0

# --- ПРОДАЖА ПРИЧИНЫ ---
def request_reason(action, user_id):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Отменить", callback_data=f"cancel_{action}_{user_id}"))
    bot.send_message(GROUP_ID, f"Укажите причину {action}:", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel"))
def cancel_action(call):
    action, user_id = call.data.split("_")[1:]
    bot.edit_message_text("Действие отменено.", call.message.chat.id, call.message.message_id)

@bot.message_handler(func=lambda m: hasattr(m, 'reply_to_message'))
def receive_reason(message):
    parent_call = message.reply_to_message
    action_type = parent_call.text.split(":")[0].strip()
    user_id = int(parent_call.text.split()[-1])
    if action_type == "Заблокировать":
        block_user(user_id, message.text)
    elif action_type == "Выдать предупреждение":
        warn_user(user_id, message.text)

# --- ОСНОВНЫЕ ОПЕРАЦИИ ---
def block_user(user_id, reason):
    warnings_db.pop(user_id, None)
    bot.send_message(user_id, f"К сожалению, вы заблокированы по причине: {reason}. Больше не сможете пользоваться ботом.")
    bot.send_message(GROUP_ID, f"Пользователь {user_id} заблокирован по причине: {reason}.")

def warn_user(user_id, reason):
    current_warnings = warnings_db.get(user_id, 0)
    next_warnings = current_warnings + 1
    warnings_db[user_id] = next_warnings
    max_warnings = 3
    level = f"{next_warnings}/{max_warnings}"
    if next_warnings == max_warnings:
        block_user(user_id, reason)
    else:
        bot.send_message(user_id, f"Вам выдано предупреждение {level} по причине: {reason}. Не нарушайте правила.")
        bot.send_message(GROUP_ID, f"Пользователь {user_id} получил предупреждение {level} по причине: {reason}.")

def check_blocked(user_id):
    return user_id in warnings_db and warnings_db[user_id] == 3

def check_active_user(message):
    if check_blocked(message.from_user.id):
        bot.send_message(message.chat.id, "К сожалению, вы заблокированы.")
        return False
    return True

# --- КОМАНДЫ ---
@bot.message_handler(commands=['start', 'auto'])
def send_welcome(message):
    logger.info(f"Command '/start' executed by user {message.from_user.id}")
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    user_data[chat_id] = {'photos': [], 'text': None}
    bot.send_message(
        chat_id,
        "Привет! Чтобы отправить объявление, нажмите на кнопку ниже 👇",
        reply_markup=get_start_kb()
    )

@bot.message_handler(func=lambda m: m.text == "Отправить объявление")
def ask_photo(message):
    logger.info(f"Button 'Отправить объявление' pressed by user {message.from_user.id}")
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    limited, remaining = is_user_limited(chat_id)
    if limited:
        bot.send_message(
            chat_id,
            f"Вы пока не можете отправить объявление. Нужно, чтобы в канале появилось еще **{remaining}** сообщения.",
            parse_mode="Markdown"
        )
        return
    user_data[chat_id] = {'photos': [], 'text': None}
    bot.send_message(
        chat_id,
        "Отправьте фотографию(ии) вашего объявления",
        reply_markup=types.ReplyKeyboardRemove()
    )

@bot.message_handler(content_types=['photo'])
def handle_photos(message):
    logger.info(f"Photo uploaded by user {message.from_user.id}")
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    if chat_id not in user_data:
        return
    if len(user_data[chat_id]['photos']) < 10:
        file_id = message.photo[-1].file_id
        user_data[chat_id]['photos'].append(file_id)
        bot.send_message(
            chat_id,
            f"Фото получено ({len(user_data[chat_id]['photos'])}/10). Можете отправить еще или закончить:",
            reply_markup=get_finish_photos_kb()
        )

@bot.message_handler(func=lambda m: m.text == "Закончить отправку фото ✅")
def finish_photos_step(message):
    logger.info(f"Button 'Закончить отправку фото' pressed by user {message.from_user.id}")
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    if chat_id not in user_data or not user_data[chat_id]['photos']:
        bot.send_message(chat_id, "Вы не отправили ни одного фото!")
        return
    bot.send_message(chat_id, "Теперь отправьте текст к вашему объявлению", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, get_text)

def get_text(message):
    logger.info(f"Text entered by user {message.from_user.id}")
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    if not message.text:
        bot.send_message(chat_id, "Пожалуйста, отправьте именно текст.")
        bot.register_next_step_handler(message, get_text)
        return
    user_data[chat_id]['text'] = message.text
    bot.send_message(
        chat_id,
        "Объявление готово к публикации, вы уверены?",
        reply_markup=get_confirm_kb()
    )

@bot.message_handler(func=lambda m: m.text in ["Готово ☑️", "Изменить"])
def confirm_step(message):
    logger.info(f"Button 'Готово' or 'Изменить' pressed by user {message.from_user.id}")
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    if chat_id not in user_data:
        return
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
        bot.send_message(chat_id, "Объявление опубликовано! Вы сможете отправить следующее через 3 сообщения в канале.")
        
        # Информация для сотрудников
        user_username = message.from_user.username if message.from_user.username else str(message.from_user.id)
        notify_text = f"Пользователь {user_username} отправил объявление:\n\n{data['text']}\n\n<i>Действия:</i>"
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton("Заблокировать", callback_data=f"block_{message.from_user.id}"),
            types.InlineKeyboardButton("Выдать предупреждение", callback_data=f"warn_{message.from_user.id}")
        )
        bot.send_message(GROUP_ID, notify_text, parse_mode="html", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error while processing announcements: {e}")
        bot.send_message(chat_id, "Ошибка при публикации объявления, попробуйте позже.")

# Обработчик обратных вызовов (действий сотрудников)
@bot.callback_query_handler(func=lambda call: True)
def employee_action_handler(call):
    logger.info(f"Callback received with data '{call.data}'")
    action, user_id = call.data.split("_")
    user_id = int(user_id)
    if action == "block":
        request_reason("Заблокировать", user_id)
    elif action == "warn":
        request_reason("Выдать предупреждение", user_id)

if __name__ == '__main__':
    logger.info("Starting the bot...")
    threading.Thread(target=run_flask, daemon=True).start()
    bot.infinity_polling()
    logger.info("Bot stopped.")
