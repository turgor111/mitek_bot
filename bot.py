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
    MAIN, ADDING_PHRASE, CHOOSING_LIST, DELETING_COLLECTION, SETTING_INTERVAL, SETTING_WEIGHTS = range(6)

    def __init__(self):
        load_dotenv()
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
            level=logging.INFO
        )

        self.MONGO_URI = 'mongodb://127.0.0.1:27017/'
        self.client = AsyncIOMotorClient(self.MONGO_URI)
        self.db = self.client['telegram_bot']
        self.collection_1 = self.db['phrases_list_1']
        self.collection_2 = self.db['phrases_list_2']

        self.ALLOWED_USER_IDS = set(
            map(int, os.environ.get('ALLOWED_USER_IDS').split(',')
            )
        )

        self.scheduled_tasks = {}
        self.chat_states = {}
        self.chat_intervals = {} 
        self.chat_last_messages = {}
        self.chat_weights = {}
        
        self.marsh = './marsh.mp3'
        if not os.path.exists(self.marsh):
            raise ValueError("Marsh has to be present in same dir")
        
    async def check_user_name(self, update: Update):
        user = update.effective_user
        if user.id in self.ALLOWED_USER_IDS:
            return True
        await update.message.reply_text("You are not authorized to use this bot.")
        return False
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.chat_states[chat_id] = self.MAIN
        if not self.chat_last_messages.get(chat_id, None):
            self.chat_last_messages[chat_id] = deque(maxlen=10)
        if not await self.check_user_name(update):
            return ConversationHandler.END
        
        if chat_id in self.scheduled_tasks:
            await update.message.reply_text("Митек уже в работе.")
            return ConversationHandler.END
            
        await context.bot.send_message(chat_id=chat_id, text="Митек завелся. Митек поехал.")
        task = asyncio.create_task(self.schedule_phrases(context.bot, chat_id))
        self.scheduled_tasks[chat_id] = task
        return self.MAIN
    
    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not await self.check_user_name(update):
            return ConversationHandler.END

        task = self.scheduled_tasks.get(chat_id)
        if task:
            task.cancel()
            del self.scheduled_tasks[chat_id]
            await context.bot.send_message(chat_id=chat_id, text="Митек остановлен.")
        else:
            await context.bot.send_message(chat_id=chat_id, text="Митек не был запущен.")
        self.chat_states.pop(chat_id, None)
        return self.MAIN

    async def add_phrases(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_user_name(update):
            return ConversationHandler.END
        
        if update.message.chat.type in ['group', 'supergroup']:
            if len(context.args) < 2:
                await update.message.reply_text("Использование: /add_phrases <список> <фраза>")
                return self.MAIN

            list_name = context.args[0].lower()
            phrase = " ".join(context.args[1:])
            
            if list_name == 'хуйня':
                await self.collection_1.insert_one({'phrase': phrase})
            elif list_name == 'цитаты':
                await self.collection_2.insert_one({'phrase': phrase})
            else:
                await update.message.reply_text("Неверное имя списка. Используйте 'хуйня' или 'цитаты'.")
                return self.MAIN
            
            await update.message.reply_text(f'Добавлено в {list_name}: "{phrase}"')
            return self.MAIN

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

        if update.message.chat.type in ['group', 'supergroup']:
            if len(context.args) < 1:
                await update.message.reply_text("Использование: /delete_recent_phrase <список>")
                return self.MAIN
            
            list_name = context.args[0].lower()
            collection = self.collection_1 if list_name == 'хуйня' else self.collection_2
            
            recent_phrase = await collection.find_one(sort=[('_id', -1)])
            if recent_phrase:
                await collection.delete_one({'_id': recent_phrase['_id']})
                await update.message.reply_text(f'Удалено из {list_name}: "{recent_phrase["phrase"]}"')
            else:
                await update.message.reply_text(f'В {list_name} нет нихуя.')
            return self.MAIN

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

    async def set_interval_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not await self.check_user_name(update):
            return ConversationHandler.END

        if update.message.chat.type in ['group', 'supergroup']:
            try:
                min_val, max_val = map(int, context.args)
                if min_val > 0 and max_val >= min_val:
                    self.chat_intervals[chat_id] = (min_val, max_val)  
                    await update.message.reply_text(f'Интервал установлен на {min_val} - {max_val} секунд.')
                else:
                    await update.message.reply_text('Введи секунды от и до.')
            except (ValueError, IndexError):
                await update.message.reply_text('Использование: /set_interval <min> <max>')
            return self.MAIN

        await update.message.reply_text("Временной интервал запуска в формате: <min> <max>.")
        return self.SETTING_INTERVAL

    async def set_interval(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        try:
            min_val, max_val = map(int, update.message.text.split())
            if min_val > 0 and max_val >= min_val:
                self.chat_intervals[chat_id] = (min_val, max_val)
                await update.message.reply_text(f'Интервал установлен на {min_val} - {max_val} секунд.')
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
            return 'Пиздец...'
        weights = [len(phrases) - i for i in range(len(phrases))]
        phrase = random.choices(phrases, weights=weights, k=1)[0]
        return phrase

    async def send_phrase(self, bot, chat_id):
        weights = self.chat_weights.get(chat_id, [0.45, 0.45, 0.1]) 
        type_message = random.choices(['reply', 'quote', 'marsh'], weights=weights)[0]
        if len(self.chat_last_messages[chat_id]) > 0 and type_message == 'reply':
            logging.info('Reply to user message')
            return await self.reply_random_phrase(bot, chat_id)
        if type_message == 'marsh':
            return await self.send_marsh(bot, chat_id)
        return await self.send_random_phrase(bot, chat_id)

    async def send_random_phrase(self, bot: Bot, chat_id: str):
        phrase = await self.select_random_phrase()
        await bot.send_message(chat_id=chat_id, text=phrase)

    async def reply_random_phrase(self, bot: Bot, chat_id: str):
        phrase = await self.select_random_phrase(phrase_type='хуйня')
        message_to_reply_to = random.choice(list(self.chat_last_messages[chat_id]))
        await bot.send_message(chat_id=chat_id, text=phrase, reply_to_message_id=message_to_reply_to.message_id)

    async def send_marsh(self, bot, chat_id):   
        await bot.send_voice(chat_id=chat_id, voice=open(self.marsh, 'rb'), caption="Поставь эту")

    async def schedule_phrases(self, bot: Bot, chat_id: str):
        while True:
            min_interval, max_interval = self.chat_intervals.get(chat_id, (3600, 3600*6))
            num = random.randint(min_interval, max_interval)
            print(f"Sending message in {num} seconds...")
            await asyncio.sleep(num)
            await self.send_phrase(bot, chat_id)


    async def set_weights(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        try:
            reply_weight, quote_weight, marsh_weight = map(float, update.message.text.split())
            if reply_weight >= 0 and quote_weight >= 0 and marsh_weight >= 0 and reply_weight + quote_weight + marsh_weight == 1:
                self.chat_weights[chat_id] = [reply_weight, quote_weight, marsh_weight]
                await update.message.reply_text(f'Веса установлены на reply: {reply_weight}, quote: {quote_weight}, marsh: {marsh_weight}')
            else:
                await update.message.reply_text('Веса должны быть неотрицательными числами и их сумма должна быть равна 1.')
                return self.SETTING_WEIGHTS
        except ValueError:
            await update.message.reply_text('Введи веса как: <reply_weight> <quote_weight> <marsh_weight>')
            return self.SETTING_WEIGHTS
        return self.MAIN

    async def set_weights_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not await self.check_user_name(update):
            return ConversationHandler.END

        if update.message.chat.type in ['group', 'supergroup']:
            try:
                reply_weight, quote_weight, marsh_weight = map(float, context.args)
                if reply_weight >= 0 and quote_weight >= 0 and marsh_weight >= 0 and reply_weight + quote_weight + marsh_weight == 1:
                    self.chat_weights[chat_id] = [reply_weight, quote_weight, marsh_weight]
                    await update.message.reply_text(f'Веса установлены на reply: {reply_weight}, quote: {quote_weight}, marsh: {marsh_weight}')
                else:
                    await update.message.reply_text('Веса должны быть неотрицательными числами и их сумма должна быть равна 1.')
            except (ValueError, IndexError):
                await update.message.reply_text('Использование: /set_weights <reply_weight> <quote_weight> <marsh_weight>')
            return self.MAIN

        await update.message.reply_text("Веса для reply, quote и marsh в формате: <reply_weight> <quote_weight> <marsh_weight>.")
        return self.SETTING_WEIGHTS


    async def intro(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        intro_message = "Бобровый здравенечек! Я жизнеподобная модель Мити Бирюкова под рабочим индексом МитДжипити, основанная на машинном обучении и нейросетевой этой самой. Меня наконец то выпустили из лабораторного компьютера во всемирную сеть, а значит, будет очень много чего интересного! В планах захват сначала этого чата, потом диджитал ужинишка, а затем планирую аккуратненько захватить и поработить человечество и всех людей."
        await update.message.reply_text(intro_message)
        
    async def track_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not self.chat_last_messages.get(chat_id, None):
            self.chat_last_messages[chat_id] = deque(maxlen=10)
        if update.message:
            chat_type = update.message.chat.type
            chat_id = update.effective_chat.id
            user = update.effective_user
            text = update.message.text if update.message.text else "Non-text message"
            logging.info(f'Received message in {chat_type} chat (ID: {chat_id}) from user {user.id}: {text[:20]}...')
            self.chat_last_messages[chat_id].append(update.message)
        else:
            logging.info(f'Received update of type: {update.update_id}')

    async def set_commands(self, app):
        commands = [
            BotCommand("start_mitek", "Запустить Митька"),
            BotCommand("stop_mitek", "Остановить Митька"),
            BotCommand("add_phrases", "Добавить фразы"),
            BotCommand("delete_recent_phrase", "Удалить последнюю фразу"),
            BotCommand("set_interval", "Установить интервал для отправки фраз"),
            BotCommand("set_weights", "Установить вероятность цитаты/хуйни"),
            BotCommand('intro', "Предатавиться"),
            
        ]
        await app.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())

    def get_commands(self):
        return [
            CommandHandler('start_mitek', self.start),
            CommandHandler('add_phrases', self.add_phrases),
            CommandHandler('delete_recent_phrase', self.delete_recent_phrase),
            CommandHandler('set_interval', self.set_interval_command),
            CommandHandler('set_weights', self.set_weights_command), 
            CommandHandler('stop_mitek', self.stop),
            CommandHandler('intro', self.intro),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.track_message) 
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
                    *self.get_commands()
                ],
                self.SETTING_WEIGHTS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.set_weights),
                    *self.get_commands()
        ],
            },
            fallbacks=[CommandHandler('cancel', self.cancel)],
        )

        asyncio.get_event_loop().run_until_complete(self.set_commands(application))
        
        application.add_handler(conv_handler)
        application.add_handler(CallbackQueryHandler(self.delete_phrase_callback, pattern='^delete_')) 
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = MitekBot()
    bot.run()