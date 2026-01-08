import os
import telebot
from telebot import types
from flask import Flask
import threading
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TG_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- ХРАНИЛИЩЕ ДАННЫХ ---
user_data = {}  # Для сбора фото и текста
# Лимиты: {user_id: next_allowed_post_number}
user_limits = {} 
# Глобальный счетчик опубликованных постов
global_post_count = 0

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

# --- ФУНКЦИИ ПРОВЕРКИ ЛИМИТА ---

def is_user_limited(user_id):
    """Проверяет, может ли пользователь отправить пост сейчас."""
    if user_id not in user_limits:
        return False, 0
    
    needed_count = user_limits[user_id]
    if global_post_count < needed_count:
        remaining = needed_count - global_post_count
        return True, remaining
    return False, 0

# --- КОМАНДЫ ---

@bot.message_handler(commands=['start', 'auto'])
def send_welcome(message):
    chat_id = message.chat.id
    user_data[chat_id] = {'photos': [], 'text': None}
    bot.send_message(
        chat_id, 
        "Привет Захар, чтобы отправить объявление нажми ниже 👇", 
        reply_markup=get_start_kb()
    )

@bot.message_handler(func=lambda m: m.text == "Отправить объявление")
def ask_photo(message):
    chat_id = message.chat.id
    
    # ПРОВЕРКА ЛИМИТА
    limited, remaining = is_user_limited(chat_id)
    if limited:
        bot.send_message(
            chat_id, 
            f"Вы пока не можете отправить объявление. ⛔️\n\nНужно, чтобы в канале вышло еще **{remaining}** объявления от других пользователей.",
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
            f"Фото получено ({len(user_data[chat_id]['photos'])}/10). Можете отправить еще или нажмите кнопку ниже 👇", 
            reply_markup=get_finish_photos_kb()
        )

# Нажатие на кнопку "Закончить отправку фото ✅"
@bot.message_handler(func=lambda m: m.text == "Закончить отправку фото ✅")
def finish_photos_step(message):
    chat_id = message.chat.id
    if chat_id not in user_data or not user_data[chat_id]['photos']:
        bot.send_message(chat_id, "Вы не отправили ни одного фото!")
        return
    
    bot.send_message(chat_id, "Теперь отправьте текст к вашему фото", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, get_text)

def get_text(message):
    chat_id = message.chat.id
    if not message.text:
        bot.send_message(chat_id, "Пожалуйста, отправьте именно текст.")
        bot.register_next_step_handler(message, get_text)
        return
    
    user_data[chat_id]['text'] = message.text
    bot.send_message(
        chat_id, 
        "Объявление готово к публикации, вы уверены? Если нужно что-то изменить нажмите ниже", 
        reply_markup=get_confirm_kb()
    )

# Кнопки Готово / Изменить
@bot.message_handler(func=lambda m: m.text in ["Готово ☑️", "Изменить"])
def confirm_step(message):
    chat_id = message.chat.id
    if chat_id not in user_data: return

    if message.text == "Изменить":
        user_data[chat_id] = {'photos': [], 'text': None}
        ask_photo(message)
        return

    # Процесс публикации
    temp_msg = bot.send_message(chat_id, "Объявление публикуется", reply_markup=get_start_kb())
    
    try:
        global global_post_count
        data = user_data[chat_id]
        photos = data['photos']
        caption = data['text']

        # Собираем альбом
        media = []
        for i, p_id in enumerate(photos):
            if i == 0:
                media.append(types.InputMediaPhoto(p_id, caption=caption))
            else:
                media.append(types.InputMediaPhoto(p_id))

        # Отправка в канал
        bot.send_media_group(CHANNEL_ID, media)

        # ПОСЛЕ УСПЕШНОЙ ОТПРАВКИ:
        global_post_count += 1 # Увеличиваем общий счетчик
        # Ставим лимит пользователю: он сможет писать снова, когда счетчик вырастет на 4
        # (это обеспечит пропуск в 3 объявления от других людей)
        user_limits[chat_id] = global_post_count + 3

        bot.delete_message(chat_id, temp_msg.message_id)
        bot.send_message(chat_id, "Объявление опубликовано! ✅\n\nСледующее вы сможете отправить после того, как в канале выйдет еще 3 объявления.")
        user_data[chat_id] = {'photos': [], 'text': None}

    except Exception as e:
        error_str = str(e).lower()
        if "chat not found" in error_str or "forbidden" in error_str:
            bot.send_message(chat_id, "Ошибка, группа закрыта, обратитесь к администратору @Ivanka58")
        else:
            bot.send_message(chat_id, "Критическая ошибка, обратитесь к администратору @Ivanka58")
        print(f"Error: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.infinity_polling()
