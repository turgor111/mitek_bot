import logging
import asyncio
import os
from telegram import (
    Update, 
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup, 
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeAllGroupChats
)
from telegram.ext import (
    CallbackContext, 
    ApplicationBuilder,
    CommandHandler, 
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler, 
    filters
)
from dotenv import load_dotenv
import random
from motor.motor_asyncio import AsyncIOMotorClient
from collections import deque

class MitekBot:
    MAIN, ADDING_PHRASE, CHOOSING_LIST, DELETING_COLLECTION, SETTING_INTERVAL = range(5)

    def __init__(self):
        load_dotenv()
        logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

        self.MONGO_URI = 'mongodb://127.0.0.1:27017/'
        self.client = AsyncIOMotorClient(self.MONGO_URI)
        self.db = self.client['telegram_bot']
        self.collection_1 = self.db['phrases_list_1']
        self.collection_2 = self.db['phrases_list_2']

        self.ALLOWED_USER_IDS = set(map(int, os.environ.get('ALLOWED_USER_IDS').split(',')))

        self.min_interval = 1
        self.max_interval = 30

        self.last_messages = deque(maxlen=10)
        self.scheduled_tasks = {}
        self.MITEK_STARTED = False

    async def check_user_name(self, update: Update):
        user = update.effective_user
        if user.id in self.ALLOWED_USER_IDS:
            return True
        await update.message.reply_text("You are not authorized to use this bot.")
        return False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_user_name(update):
            return ConversationHandler.END
        
        if self.MITEK_STARTED:
            await update.message.reply_text("Митек уже в работе.")
            return ConversationHandler.END
            
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id=chat_id, text="Митек завелся. Митек поехал.")
        task = asyncio.create_task(self.schedule_phrases(context.bot, chat_id))
        self.scheduled_tasks[chat_id] = task
        self.MITEK_STARTED = True
        return self.MAIN

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_user_name(update):
            return ConversationHandler.END
        chat_id = update.effective_chat.id
        self.MITEK_STARTED = False
        task = self.scheduled_tasks.get(chat_id)
        if task:
            task.cancel()
            del self.scheduled_tasks[chat_id]
            await context.bot.send_message(chat_id=chat_id, text="Митек остановлен.")
        else:
            await context.bot.send_message(chat_id=chat_id, text="Митек не был запущен.")
        return self.MAIN

    async def start_add_phrases(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_user_name(update):
            return ConversationHandler.END
        await update.message.reply_text("Добавь фразу для МитGPT.")
        return self.ADDING_PHRASE

    async def ask_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['new_phrase'] = update.message.text
        keyboard = [
            [InlineKeyboardButton("Хуйня", callback_data='add_хуйня'),
             InlineKeyboardButton("Цитаты", callback_data='add_цитаты')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Добавить фразу в хуйню или цитаты?", reply_markup=reply_markup)
        return self.CHOOSING_LIST

    async def add_phrase_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        list_name = query.data.split('_')[1]
        phrase = context.user_data.get('new_phrase')
        
        if list_name == 'хуйня':
            await self.collection_1.insert_one({'phrase': phrase})
        elif list_name == 'цитаты':
            await self.collection_2.insert_one({'phrase': phrase})
        
        await query.edit_message_text(f'Добавлено в {list_name}: "{phrase}"')
        return self.MAIN

    async def delete_recent_phrase(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_user_name(update):
            return ConversationHandler.END
        keyboard = [
            [InlineKeyboardButton("Хуйня", callback_data='delete_хуйня'),
             InlineKeyboardButton("Цитаты", callback_data='delete_цитаты')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Откуда удалить последнюю фразу?", reply_markup=reply_markup)
        return self.MAIN

    async def delete_phrase_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        list_name = query.data.split('_')[1]
        collection = self.collection_1 if list_name == 'хуйня' else self.collection_2

        recent_phrase = await collection.find_one(sort=[('_id', -1)])
        if recent_phrase:
            await collection.delete_one({'_id': recent_phrase['_id']})
            await query.edit_message_text(f'Удалено из {list_name}: "{recent_phrase["phrase"]}"')
        else:
            await query.edit_message_text(f'В {list_name} нет нихуя.')
        return self.MAIN

    async def start_set_interval(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_user_name(update):
            return ConversationHandler.END
        await update.message.reply_text("Временной интервал запуска в формате: <min> <max>.")
        return self.SETTING_INTERVAL

    async def set_interval(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            min_val, max_val = map(int, update.message.text.split())
            if min_val > 0 and max_val >= min_val:
                self.min_interval = min_val
                self.max_interval = max_val
                await update.message.reply_text(f'Интервал установлен на {self.min_interval} - {self.max_interval} секунд.')
            else:
                await update.message.reply_text('Введи секунды от и до.')
                return self.SETTING_INTERVAL
        except ValueError:
            await update.message.reply_text('Числа блять!!!')
            return self.SETTING_INTERVAL
        return self.MAIN

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Операция отменена.")
        return self.MAIN

    async def select_random_phrase(self, phrase_type=None):
        phrases_1 = await self.collection_1.find().to_list(length=None)
        phrases_2 = await self.collection_2.find().to_list(length=None)
        if not phrase_type: 
            phrases = [doc['phrase'] for doc in phrases_1 + phrases_2]
        elif phrase_type == 'хуйня':
            phrases = [doc['phrase'] for doc in phrases_1]    
        elif phrase_type == 'цитаты':
           phrases = [doc['phrase'] for doc in phrases_2]
        if not phrases:
            return "No phrases available."
        weights = [len(phrases) - i for i in range(len(phrases))]
        phrase = random.choices(phrases, weights=weights, k=1)[0]
        return phrase

    async def send_phrase(self, bot, chat_id):
        type_message = random.choices(['reply', 'quote'], weights=[0.85, 0.15])[0]
        if len(self.last_messages) > 0 and type_message == 'reply':
            logging.info('Reply to user message')
            return await self.reply_random_phrase(bot, chat_id)
        return await self.send_random_phrase(bot, chat_id)

    async def send_random_phrase(self, bot: Bot, chat_id: str):
        phrase = await self.select_random_phrase()
        await bot.send_message(chat_id=chat_id, text=phrase)

    async def reply_random_phrase(self, bot: Bot, chat_id: str):
        phrase = await self.select_random_phrase(phrase_type='хуйня')
        message_to_reply_to = random.choice(list(self.last_messages))
        await bot.send_message(chat_id=chat_id, text=phrase, reply_to_message_id=message_to_reply_to.message_id)

    async def schedule_phrases(self, bot: Bot, chat_id: str):
        while True:
            await asyncio.sleep(random.randint(self.min_interval, self.max_interval))
            await self.send_phrase(bot, chat_id)

    async def track_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_user_name(update):
            return
        logging.info(f'Added message {len(self.last_messages)}')
        self.last_messages.append(update.message)
        return self.MAIN

    async def set_private_commands(self, app):
        commands = [
            BotCommand("start_mitek", "Запустить Митька"),
            BotCommand("stop_mitek", "Остановить Митька"),
            BotCommand("add_phrases", "Добавить фразы"),
            BotCommand("delete_recent_phrase", "Удалить последнюю фразу"),
            BotCommand("set_interval", "Установить интервал для отправки фраз"),
        ]
        await app.bot.set_my_commands(commands, scope=BotCommandScopeDefault())

    async def set_group_commands(self, app):
        commands = [
            BotCommand("start_mitek", "Запустить Митька"),
            BotCommand("stop_mitek", "Остановить Митька"),
        ]
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
    
    def get_commands(self):
        return [
            CommandHandler('start_mitek', self.start),
            CommandHandler('add_phrases', self.start_add_phrases),
            CommandHandler('delete_recent_phrase', self.delete_recent_phrase),
            CommandHandler('set_interval', self.start_set_interval),
            CommandHandler('stop_mitek', self.stop) 
        ]
    
    def run(self):
        bot_token = os.environ.get('bottoken')
        if not bot_token:
            logging.error("Bot token not found in environment variables.")
            return

        application = ApplicationBuilder().token(bot_token).build()
        conv_handler = ConversationHandler(
            entry_points=self.get_commands(),
            states={
                self.MAIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.track_message),
                    *self.get_commands()
                ],
                self.ADDING_PHRASE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.ask_list),
                    *self.get_commands()
                ],
                self.CHOOSING_LIST: [
                    CallbackQueryHandler(self.add_phrase_callback, pattern='^add_'),
                    *self.get_commands()
                ],
                self.SETTING_INTERVAL: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.set_interval),
                ],
            },
            fallbacks=[CommandHandler('cancel', self.cancel)],
        )

        asyncio.get_event_loop().run_until_complete(self.set_group_commands(application))
        asyncio.get_event_loop().run_until_complete(self.set_private_commands(application))
        
        application.add_handler(conv_handler)
        application.add_handler(CallbackQueryHandler(self.delete_phrase_callback, pattern='^delete_')) 
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = MitekBot()
    bot.run()