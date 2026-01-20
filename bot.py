import os
import telebot
from telebot import types
from flask import Flask
import threading
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TG_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # Убедись, что это ID канала (начинается с -100)
GROUP_ID = os.getenv("GROUP_ID")       # Идентификатор группы сотрудников (добавлена новая переменная)

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- ХРАНИЛИЩЕ ДАННЫХ ---
user_data = {}          # Данные объявления
user_limits = {}        # Ограничения пользователей
warnings_db = {}        # Количество предупреждений для пользователей
global_msg_count = 0    # Общий счетчик всех сообщений в канале

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

# Этот обработчик ловит ВСЕ сообщения в канале, где бот является админом
@bot.channel_post_handler()
def listen_channel(message):
    global global_msg_count
    # Проверяем, что сообщение именно из нашего целевого канала
    if str(message.chat.id) == str(CHANNEL_ID):
        global_msg_count += 1
        print(f"Счетчик канала увеличился: {global_msg_count}")

# --- ФУНКЦИИ ПРОВЕРКИ ЛИМИТА ---

def is_user_limited(user_id):
    if user_id not in user_limits:
        return False, 0
    
    needed_count = user_limits[user_id]
    if global_msg_count < needed_count:
        remaining = needed_count - global_msg_count
        return True, remaining
    return False, 0

# --- ОБРАБОТКА ПОДПИСОК НА БЛОКПОСТЫ И ПРЕДУПРЕЖДЕНИЯ ---

# Эта функция вызывает блокировку пользователя
def block_user(user_id, reason):
    # Убираем предупреждения
    warnings_db.pop(user_id, None)
    # Сообщаем пользователю о блокировке
    bot.send_message(user_id, f"К сожалению, вы заблокированы по причине: {reason}.\nБольше не сможете пользоваться ботом.")
    # Информируем сотрудников
    bot.send_message(GROUP_ID, f"Пользователь {user_id} заблокирован по причине: {reason}.")

# Эта функция выдает предупреждение пользователю
def warn_user(user_id, reason):
    current_warnings = warnings_db.get(user_id, 0)
    next_warnings = current_warnings + 1
    warnings_db[user_id] = next_warnings
    max_warnings = 3
    level = f"{next_warnings}/{max_warnings}"
    
    if next_warnings == max_warnings:
        # Последний уровень предупреждений ведет к блокировке
        block_user(user_id, reason)
    else:
        # Обычное предупреждение
        bot.send_message(user_id, f"Вам выдано предупреждение {level} по причине: {reason}.\nНе нарушайте правила.")
        bot.send_message(GROUP_ID, f"Пользователь {user_id} получил предупреждение {level} по причине: {reason}.")

# Проверка блокировки пользователя
def check_blocked(user_id):
    return user_id in warnings_db and warnings_db[user_id] == 3

# Основная проверка активности пользователя (используется в каждом обработчике)
def check_active_user(message):
    if check_blocked(message.from_user.id):
        bot.send_message(message.chat.id, "К сожалению, вы заблокированы.")
        return False
    return True

# Вспомогательная функция ожидания ввода причины
def wait_for_reason(chat_id):
    msg = bot.send_message(chat_id, "Укажите причину:", reply_markup=types.ForceReply())
    return bot.register_next_step_handler(msg, lambda m: m.text)

# Обработчик сообщений с причиной блокировки или предупреждения
@bot.message_handler(func=lambda m: hasattr(m, 'reply_to_message'))
def handle_reason(message):
    parent_call = message.reply_to_message
    action_type = parent_call.json["text"][:6].strip()  # Берем первую часть текста родительского сообщения
    user_id = int(parent_call.json["text"].split()[1])  # Извлекаем идентификатор пользователя
    
    if action_type == "Заблокир":
        block_user(user_id, message.text)
    elif action_type == "Выдать пред":
        warn_user(user_id, message.text)

# Обработчик выбора действий сотрудником
@bot.callback_query_handler(func=lambda call: True)
def employee_action_handler(call):
    action, user_id = call.data.split("_")
    user_id = int(user_id)
    # Формируем сообщение с ожиданием причины
    text = ""
    if action == "block":
        text = f"Заблокировать пользователя {user_id}? Укажите причину:"
    elif action == "warn":
        text = f"Выдать предупреждение пользователю {user_id}? Укажите причину:"
    
    # Показываем кнопку отмены
    cancel_button = types.InlineKeyboardMarkup()
    cancel_button.add(types.InlineKeyboardButton("Отменить", callback_data=f"cancel_{action}_{user_id}"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=cancel_button)

# Обработчик отмены операции
@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel"))
def cancel_action(call):
    _, action, user_id = call.data.split("_")
    bot.edit_message_text("Операция отменена.", call.message.chat.id, call.message.message_id)

# --- КОМАНДЫ ---

@bot.message_handler(commands=['start', 'auto'])
def send_welcome(message):
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
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    
    # ПРОВЕРКА ЛИМИТА
    limited, remaining = is_user_limited(chat_id)
    if limited:
        bot.send_message(
            chat_id, 
            f"Вы пока не можете отправить объявление. ⛔️\n\nНужно, чтобы в канале появилось еще **{remaining}** сообщения (объявления или просто посты).",
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
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    if chat_id not in user_data: return

    if len(user_data[chat_id]['photos']) < 10:
        file_id = message.photo[-1].file_id
        user_data[chat_id]['photos'].append(file_id)
        
        bot.send_message(
            chat_id, 
            f"Фото получено ({len(user_data[chat_id]['photos'])}/10). Можете отправить еще или нажмите кнопку ниже 👇", 
            reply_markup=get_finish_photos_kb()
        )

# Нажатие на кнопку "Закончить отправку фото ✅"
@bot.message_handler(func=lambda m: m.text == "Закончить отправку фото ✅")
def finish_photos_step(message):
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    if chat_id not in user_data or not user_data[chat_id]['photos']:
        bot.send_message(chat_id, "Вы не отправили ни одного фото!")
        return
    
    bot.send_message(chat_id, "Теперь отправьте текст к вашему объявлению", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, get_text)

def get_text(message):
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
        "Объявление готово к публикации, вы уверены? Если нужно что-то изменить, нажмите кнопку ниже", 
        reply_markup=get_confirm_kb()
    )

# Кнопки Готово / Изменить
@bot.message_handler(func=lambda m: m.text in ["Готово ☑️", "Изменить"])
def confirm_step(message):
    if not check_active_user(message):
        return
    chat_id = message.chat.id
    if chat_id not in user_data: return

    if message.text == "Изменить":
        user_data[chat_id] = {'photos': [], 'text': None}
        ask_photo(message)
        return

    # Публикация
    temp_msg = bot.send_message(chat_id, "Объявление публикуется", reply_markup=get_start_kb())
    
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

        # Отправляем альбом
        bot.send_media_group(CHANNEL_ID, media)

        # Ставим лимит: текущий счетчик + 3 сообщения сверху
        # (Счетчик сам увеличится, когда бот увидит свой же пост в канале через channel_post_handler)
        user_limits[chat_id] = global_msg_count + 4 

        bot.delete_message(chat_id, temp_msg.message_id)
        bot.send_message(chat_id, "Объявление опубликовано! ✅\n\nВы сможете отправить следующее через 3 сообщения в канале.")

        # ИНФОРМАЦИЯ ДЛЯ СОТРУДНИКОВ
        # Отправляем уведомление в группу сотрудников
        user_username = message.from_user.username if message.from_user.username else str(message.from_user.id)
        notify_text = f"📌 Пользователь {user_username} отправил объявление:\n\n{data['text']}\n\n<i>Действия:</i>"
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton("Заблокировать", callback_data=f"block_{message.from_user.id}"),
            types.InlineKeyboardButton("Выдать предупреждение", callback_data=f"warn_{message.from_user.id}")
        )
        bot.send_message(GROUP_ID, notify_text, parse_mode="html", reply_markup=keyboard)

    except Exception as e:
        error_str = str(e).lower()
        if "chat not found" in error_str or "forbidden" in error_str:
            bot.send_message(chat_id, "Ошибка, группа закрыта, обратитесь к администратору @Ivanka58")
        else:
            bot.send_message(chat_id, "Критическая ошибка, обратитесь к администратору @Ivanka58")
        print(f"Error: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    print("Бот запущен и мониторит канал...")
    bot.infinity_polling()
