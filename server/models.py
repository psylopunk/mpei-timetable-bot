from telebot import TeleBot, types, apihelper
import datetime
import requests
import asyncio
import pymongo
import threading
import uuid
import config
import qrcode
import httpx
import time

bot = TeleBot(config.TELEGRAM_BOT_KEY)
db = pymongo.MongoClient(config.MONGO_URI).mpeitt
lastdown = datetime.datetime.now()

def get_default_inline_keyboard(user):
    return get_inline_keyboard([ \
        [{"text": "Мое расписание", "callback_data": "timetable_mem"}] if user.group_id else [], \
        [{"text": "Расположение корпусов", "callback_data": "building_locations"}],
        [{"text": "Звонки", "callback_data": "bells_sticker"}],
        [{"text": "Найти группу" if not user.group_id else "Изменить группу", "callback_data": "timetable_search"}, {"text": "Настройки", "callback_data": "settings"} if user.group_id else {}],
        [{"text": "Поделиться с друзьями", "callback_data": "share"}, {"text": "О боте", "callback_data": "feedback"}] \
    ], row_width=2)

def get_inline_keyboard(rows, *args, **kwargs):
    keyboard = types.InlineKeyboardMarkup(*args, **kwargs)
    for row in rows:
        keyboard.add(*[types.InlineKeyboardButton(text=btn["text"],  \
            callback_data=(btn["callback_data"] if "callback_data" in btn else None), url=(btn["url"] if "url" in btn else None)) for btn in row if btn])
    return keyboard

def get_keyboard(rows, **kwargs):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, **kwargs)
    for row in rows: keyboard.add(*[types.KeyboardButton(button) for button in row])
    return keyboard

def get_weekday_name(date_obj): return ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][date_obj.weekday()]

def get_group_id(name):
    try: res = requests.get("http://ts.mpei.ru/api/search", {"term": name, "type": "group"}).json()
    except Exception as e: print("Error: [%s] (caused by get_group_id)" % e); return False
    if len(res) == 1: return res[0]["id"], res[0]["label"]
    else: return False, False

class Memory:
    def log(self, text): print("[Memory] %s" % text)

    def __init__(self):
        self.db = db
        self.users = {}

        # self.loop.create_task(self.__polling_notifier__())
        # self.loop.create_task(self.__autoclear_memory__())

        db_res = db.memory.find({"key": "last_update_id"})
        try: self.last_update_id = db_res[0]["value"]
        except IndexError:
            self.last_update_id = 0
            db.memory.insert_one({"key": "last_update_id", "value": 0})

    def set_last_update_id(self, update_id):
        self.last_update_id = update_id
        db.memory.update_one({"key": "last_update_id"}, {"$set": {"value": update_id}})

    def hard_update_user(self, user): return User(user.tid)

    def get_user_by_chat(self, chat):
        if chat["id"] not in self.users:
            if db.users.count_documents({"tid": chat["id"]}) == 0:
                user_object = {"tid": chat["id"], "balance": 0.0}
                for key in ["first_name", "last_name", "username", "phone"]:
                    if key in chat: user_object[key] = chat[key]
                db.users.insert_one(user_object)
                user = User(user_object["tid"])
            else: user = User(chat["id"])
            self.users[chat["id"]] = user
        else: user = self.users[chat["id"]]
        print("[%s] Query by %s" % (datetime.datetime.now().strftime("%H:%M:%S"), user))
        return user

    # async def __polling_notifier__(self):
    #     while True:
    #         self.log("Polling Notifier work")
    #         # group_ids = {user["group_id"] for user in self.db.users.find({}) if "group_id" in user}
    #         # for
    #         await asyncio.sleep(180)
    #
    # async def __autoclear_memory__(self):
    #     self.log("AutoClear started")
    #     while True:
    #         async with self.lock: self.users = {}
    #         self.log("Active users was cleared")
    #         await asyncio.sleep(3600)

class User:
    def log(self, text): print("[%s] %s" % (self.__str__(), text))
    def __str__(self): return "User(tid=%s, username=%s, first_name=%s, last_name=%s)" % (self.tid, self.username, self.first_name, self.last_name)

    def __init__(self, tid):
        user_object = db.users.find({"tid": tid})[0]
        self.db_id = user_object["_id"]
        self.tid = user_object["tid"]
        self.username = user_object["username"] if "username" in user_object else None
        self.first_name = user_object["first_name"] if "first_name" in user_object else None
        self.last_name = user_object["last_name"] if "last_name" in user_object else None
        self.group = user_object["group"] if "group" in user_object else None
        self.group_id = user_object["group_id"] if "group_id" in user_object else None
        self.message_id = user_object["message_id"] if "message_id" in user_object else None
        self.history_messages_id = user_object["history_messages_id"] if "history_messages_id" in user_object else []
        self.last_update_id = 0

        db.users.update_one({"_id": self.db_id}, {"$set": {"last_use": datetime.datetime.now()}})

        if "settings" in user_object: self.settings = user_object["settings"]
        else:
            self.settings = {}
            self.upload_settings()

        self.clear_action()

    def check_update_id(self, uid):
        if uid > self.last_update_id:
            self.last_update_id = uid
            return True
        else: return False

    def clear_action(self):
        self.action = None
        self.data = {}

    def clear_messages(self):
        for message_id in self.history_messages_id:
            self.delete_message(message_id)
        self.history_messages_id = []
        db.users.update_one({"_id": self.db_id}, {"$set": {"history_messages_id": []}})

    def save_message(self, message_id):
        self.history_messages_id.append(message_id)
        db.users.update_one({"_id": self.db_id}, {"$set": {"history_messages_id": self.history_messages_id}})

    def upload_settings(self): db.users.update_one({"_id": self.db_id}, {"$set": {"settings": self.settings}})

    def set_group(self, group, group_id):
        self.group = group.upper()
        self.group_id = group_id
        db.users.update_one({"_id": self.db_id}, {"$set": {"group": self.group, "group_id": self.group_id}})

    def send_message(self, message, save=True, *args, **kwargs):
        try:
            r = bot.send_message(self.tid, message, parse_mode="html", *args, **kwargs)
            if save: self.save_message(r.message_id)
            return r
        except apihelper.ApiException as e: print("Error: [%s] (caused by send_message)" % e); return False

    def send_sticker(self, sticker_id, save=True, clsm=True, *args, **kwargs):
        if clsm: self.clear_messages()
        try:
            r = bot.send_sticker(self.tid, sticker_id, *args, **kwargs)
            if save: self.save_message(r.message_id)
            return r
        except apihelper.ApiException as e: print("Error: [%s] (caused by send_sticker)" % e); return False

    def send_photo(self, photo, save=True):
        try:
            r = bot.send_photo(self.tid, photo)
            if save: self.save_message(r.message_id)
            return r
        except apihelper.ApiException as e: print("Error: [%s] (caused by send_photo)" % e); return False

    def send_location(self, latitude, longitude, save=True):
        try:
            r = bot.send_location(self.tid, latitude, longitude)
            if save: self.save_message(r.message_id)
            return r
        except apihelper.ApiException as e: print("Error: [%s] (caused by send_location)" % e); return False

    def delete_message(self, message_id):
        try: bot.delete_message(self.tid, message_id)
        except apihelper.ApiException as e: print("Error: [%s] (caused by delete_message)" % e)

    def edit_message(self, text, *args, **kwargs):
        self.clear_messages()
        try:
            # self.delete_message(self.message_id)
            # r = self.send_message(text, save=False, *args, **kwargs)
            # if r:
            #     self.message_id = r.message_id
            #     db.users.update_one({"_id": self.db_id}, {"$set": {"message_id": r.message_id}})
            # return r
            return bot.edit_message_text(chat_id=self.tid, message_id=self.message_id, \
            text=text, parse_mode="html", *args, **kwargs)
        except apihelper.ApiException as e:
            print("Error: [%s] (caused by edit_message)" % e)
            return False

    def answer_callback(self, cd_id, text=None):
        try: bot.answer_callback_query(callback_query_id=cd_id, text=(text or "Выполнено"), show_alert=False)
        except apihelper.ApiException as e: print("Error: [%s] (caused by answer_callback)" % e)

    def send_settings(self):
        if "lesson_notification" not in self.settings:
            self.settings["lesson_notification"] = {"enabled": False}
            self.upload_settings()
        self.clear_messages()
        self.edit_message("""⚙️ <b>Настройки</b>

<b>Уведомления о парах</b>
<i>Вы можете установить время, за сколько перед началом пары, Вам нужно будет прислать сообщение</i>

⚠️ <b>Уведомления о парах еще не доступны, Вы можете включить эту настройку заранее</b>""", reply_markup=get_inline_keyboard([ \
            [{"text": "%s Уведомления об парах" % ("🟢" if self.settings["lesson_notification"]["enabled"] else "🔴"), "callback_data": "setting_toggle_lnotification"}], \
            [{"text": "На главную 🔙", "callback_data": "home"}]
        ]))

    async def send_timetable(self, date_obj):
        day = await self.get_timetable_json(date_obj)
        lessons_message = ""
        time_now = datetime.datetime.now()
        for lesson in day:
            if time_now < lesson["beginLesson"]:
                lessons_message += "⚪️ "
            elif time_now > lesson["beginLesson"] and time_now < lesson["endLesson"]:
                lessons_message += "🟡 "
            elif time_now > lesson["endLesson"]:
                lessons_message += "🟢 "
            lessons_message += """<b>%s</b>
      <i>%s - %s</i>
      📍 %s
      👨‍🏫 %s
      <code>%s</code>

""" % (lesson["name"], lesson["beginLesson"].strftime("%H:%M"), lesson["endLesson"].strftime("%H:%M"), \
                lesson["place"], lesson["lecturer"] if "!" not in lesson["lecturer"] else "<i>Нет информации</i>", lesson["type"])
        return self.edit_message("""🔰 <b>Расписание на %s, %s</b>
<i>Информация обновлена %s</i>

%s🟡 <b>Пара идет</b>
🟢 <b>Пара закончилась</b>""" % (date_obj.strftime("%d.%m"), get_weekday_name(date_obj), \
        time_now.strftime("%H:%M"), lessons_message if lessons_message else "🌀 <b>В этот день нет занятий</b>\n\n" \
        ), reply_markup=get_inline_keyboard([ \
            [ \
                {"text": "◀️ %s, %s" % ((date_obj - datetime.timedelta(days=1)).strftime("%d.%m"), get_weekday_name(date_obj - datetime.timedelta(days=1))), "callback_data": "timetable_mem_%s" % int((date_obj - datetime.timedelta(days=1)).timestamp())}, \
                {"text": "Обновить", "callback_data": "timetable_mem_%s" % int(date_obj.timestamp())},
                {"text": "%s, %s ▶️" % ((date_obj + datetime.timedelta(days=1)).strftime("%d.%m"), get_weekday_name(date_obj + datetime.timedelta(days=1))), "callback_data": "timetable_mem_%s" % int((date_obj + datetime.timedelta(days=1)).timestamp())} \
            ], \
            [ \
                {"text": "⏪ %s, %s" % ((date_obj - datetime.timedelta(days=7)).strftime("%d.%m"), get_weekday_name(date_obj - datetime.timedelta(days=7))), "callback_data": "timetable_mem_%s" % int((date_obj - datetime.timedelta(days=7)).timestamp())},
                {"text": "Сегодня", "callback_data": "timetable_mem"} if datetime.datetime.now().strftime("%d.%m.%Y") != date_obj.strftime("%d.%m.%Y") else {},
                {"text": "%s, %s ⏩" % ((date_obj + datetime.timedelta(days=7)).strftime("%d.%m"), get_weekday_name(date_obj + datetime.timedelta(days=7))), "callback_data": "timetable_mem_%s" % int((date_obj + datetime.timedelta(days=7)).timestamp())}
            ], \
            [{"text": "На главную 🔙", "callback_data": "home"}] \
        ], row_width=3))

    def send_share(self):
        # qr = qrcode.QRCode(version=4, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=3)
        # qr.add_data("https://t.me/mpei_timetable_bot%s" % (("?start=%s" % self.group.encode("utf8").hex()) if self.group else ""))
        # qr_file = "%s" % uuid.uuid4()
        # qr.make_image(fill_color="black", back_color="white").save("/data/qr_codes/%s.png" % qr_file)
        self.edit_message("""💎 <b>Поделиться с друзьями</b>

Отправь это сообщение целиком или ссылку отдельно в беседу своей группы или другу, с которым хочешь поделиться

%s""" % ("https://t.me/mpei_timetable_bot%s" % (("?start=%s" % self.group.encode("utf8").hex()) if self.group else "")), disable_web_page_preview=True, reply_markup=get_inline_keyboard([[{"text": "На главную 🔙", "callback_data": "home"}]]))
        # with open("/data/qr_codes/%s.png" % qr_file, "rb") as file: self.send_photo(file)

    def send_welcome(self, message=None):
        self.clear_action()
        self.clear_messages()
        if self.message_id: self.delete_message(self.message_id)
        m = self.send_message("""%s

%s

Выбери пункт ниже 👇""" % ( \
                (message if message else "💎 <b>Привет, здесь ты можешь найти расписание групп МЭИ</b>"), \
                ("👥 Ваша группа: <b>%s</b>" % self.group if self.group else "⚠️ <b>Группа не выбрана</b>\n<i>Найдите свою группу с помощью кнопки под сообщением для начала работы</i>") \
             ), save=False, reply_markup=get_default_inline_keyboard(self))
        if m:
            self.message_id = m.message_id
            db.users.update_one({"_id": self.db_id}, {"$set": {"message_id": m.message_id}})

    async def get_timetable_json(self, date_obj):
        global lastdown
        if not self.group_id: return False

        if (datetime.datetime.now() - lastdown).total_seconds() < 60:
            print((datetime.datetime.now() - lastdown).total_seconds())
            raise Exception('down')

        datestrf = date_obj.strftime("%Y.%m.%d")
        # TODO request exceptions
        try:
            async with httpx.AsyncClient() as client:
                res = (await client.get("http://ts.mpei.ru/api/schedule/group/%s" % self.group_id, params={"start": datestrf, "finish": datestrf, "lng": 1})).json()
        except Exception as e:
            lastdown = datetime.datetime.now()
            raise e
        # print(res)
        lessons = []
        for lesson in res:
            lesson_obj = {}
            lesson_obj["name"] = lesson["discipline"]
            lesson_obj["type"] = lesson["kindOfWork"]
            lesson_obj["place"] = "%s (%s)" % (lesson["auditorium"], lesson["building"] if "building" in lesson else "нет информации")
            lesson_obj["lecturer"] = lesson["lecturer"]
            lesson_obj["beginLesson"] = date_obj.replace(hour=int(lesson["beginLesson"].split(":")[0]), minute=int(lesson["beginLesson"].split(":")[1]))
            lesson_obj["endLesson"] = date_obj.replace(hour=int(lesson["endLesson"].split(":")[0]), minute=int(lesson["endLesson"].split(":")[1]))
            lessons.append(lesson_obj)
        return lessons
