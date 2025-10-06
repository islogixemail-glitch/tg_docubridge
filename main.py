import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import os

# Получаем токен из переменной окружения
bot_token = os.getenv('BOT_TOKEN')
if not bot_token:
    print("ERROR: BOT_TOKEN environment variable not set!")
    print("Please add your Telegram bot token to Render environment variables.")
    exit(1)

bot = telebot.TeleBot(bot_token)

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton('/consult'), KeyboardButton('/ua_ru'))
    markup.add(KeyboardButton('/eu_ua'), KeyboardButton('/news'))
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id, 
        "Добро пожаловать в IS-Logix Bot! 😊\n"
        "Мы помогаем с доставкой документов между Украиной, Россией, Беларусью и Европой, несмотря на сложности.\n"
        "Выберите опцию:", 
        reply_markup=main_menu()
    )

@bot.message_handler(commands=['consult'])
def consult(message):
    bot.send_message(
        message.chat.id, 
        "Расскажите о вашем запросе: какой документ, откуда и куда? "
        "(Например: 'Доверенность из Киева в Москву')"
    )
    bot.register_next_step_handler(message, save_lead)

def save_lead(message):
    username = message.from_user.username if message.from_user.username else "Unknown"
    user_id = message.from_user.id
    
    # Сохраняем лид
    with open('leads.txt', 'a', encoding='utf-8') as f:
        f.write(f"User: @{username} (ID: {user_id}), Query: {message.text}\n")
    
    bot.send_message(
        message.chat.id, 
        "Спасибо! Мы свяжемся с вами скоро. "
        "Пока посмотрите новости: https://www.is-logix.com/section/novosti/"
    )
    bot.send_message(message.chat.id, "Вернуться в меню?", reply_markup=main_menu())

@bot.message_handler(commands=['ua_ru'])
def ua_ru(message):
    bot.send_message(
        message.chat.id, 
        "Доставка из Украины в Россию: Несмотря на ситуацию, помогаем с доставкой различных документов. "
        "Есть некоторые ограничения.\n"
        "Гайд: https://www.is-logix.com/section/novosti/\n"
        "Нужна помощь? /consult"
    )

@bot.message_handler(commands=['eu_ua'])
def eu_ua(message):
    bot.send_message(
        message.chat.id, 
        "Доставка документов из Европы в Украину: Визы, сертификаты, безопасно.\n"
        "Подробности: https://www.is-logix.com/section/novosti/\n"
        "Консультация: /consult"
    )

@bot.message_handler(commands=['news'])
def news(message):
    bot.send_message(
        message.chat.id, 
        "Последние новости по логистике: Изменения в санкциях 2025\n"
        "Ссылка: https://www.is-logix.com/section/novosti/\n"
        "Подписывайтесь на канал: https://t.me/doki_iz_UA_v_RU_BY"
    )

@bot.message_handler(func=lambda message: True)
def echo(message):
    if 'консультация' in message.text.lower():
        consult(message)
    else:
        bot.send_message(message.chat.id, "Не понял. Выберите команду из меню.", reply_markup=main_menu())

if __name__ == '__main__':
    print("IS-Logix Bot is running on ...")
    bot.remove_webhook()
    bot.polling(none_stop=True)
