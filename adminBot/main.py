import asyncio
import logging
import sys
import os
import re
import csv
from string import digits
from datetime import timedelta, datetime

import mysql.connector
from mysql.connector import Error

from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (Message, KeyboardButton, InlineKeyboardButton,
                           FSInputFile)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext


from curl_cffi.requests import AsyncSession

from config import BOT_TOKEN, GROUP_ID, BOT_USERNAME, DB_CONFIG, ADMINS_ID, WALLET_ADDRESS

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# Set up logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Log to file
file_handler = logging.FileHandler('bot.log')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# root_logger= logging.getLogger()
# root_logger.addHandler(file_handler)

bot = Bot(token=BOT_TOKEN)

dp = Dispatcher(storage=MemoryStorage())

# User registration states


class Registration(StatesGroup):
    twitter_id = State()
    telegram_id = State()
    age = State()
    city = State()
    gender = State()
    purpose = State()

# Admin promote states


class AdminPromote(StatesGroup):
    forward_id = State()
    level = State()


class Upgrade(StatesGroup):
    txn_hash = State()

################################################ Database functions ####################################


def create_connection():
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection
    except Error as e:
        print(f'Error connecting to MySQL database: {e}')
        return None


def execute_query(connection, query, params=None):
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params) if params else cursor.execute(query)

            if query.strip().upper().startswith("SELECT"):
                return cursor.fetchall()
            else:
                connection.commit()
                return cursor.rowcount

    except Error as e:
        logging.error(f"Error in query: {e}")
        return None


def get_users_from_db():
    users = []
    connection = create_connection()
    file_path = 'user_info.csv'
    if connection:
        try:
            query = "SELECT * FROM users"
            rows = execute_query(connection, query)
            if rows:
                
                # ایجاد فایل CSV
                with open(file_path, mode='w', newline='', encoding='utf-8-sig') as file:
                    writer = csv.writer(file)
                    writer.writerow(['ID', 'Twitter ID', 'Telegram ID', 'Age', 'City', 'Gender', 'Purpose', 'Access Level', 'Registration Date'])  # هدر فایل
                    writer.writerows(rows)
        finally:
            connection.close()
    return file_path


################################################ Helper functions ######################################


async def is_user_registered(user_id):
    conn = create_connection()
    if conn is not None:
        try:
            query = 'SELECT * FROM users WHERE id = %s'
            result = execute_query(conn, query, (user_id,))
            if result:
                return True
        finally:
            conn.close()
    return False


async def get_access_levels():
    conn = create_connection()
    if conn is not None:
        try:
            query = 'SELECT level, price FROM levels ORDER BY level'
            result = execute_query(conn, query)
            if result:
                return result
        finally:
            conn.close()
    return False


async def get_user_access_level(user_id):
    conn = create_connection()
    if conn is not None:
        try:
            query = 'SELECT access_level FROM users WHERE id = %s'
            result = execute_query(conn, query, (user_id,))
            return result[0][0] if result else None
        finally:
            conn.close()
    return None


async def check_message_limits(user_id, message_type):
    access_level = await get_user_access_level(user_id)
    if access_level is None:
        return False

    conn = create_connection()
    if conn is not None:
        try:

            query = '''
                SELECT text_limit, gif_limit, photo_limit, video_limit, 
                    video_note_limit, voice_limit 
                FROM levels 
                WHERE level = %s
            '''
            limits = execute_query(conn, query, (access_level,))

            if not limits:
                logging.error(
                    f"No limits found for access level {access_level}")
                return False

            limit_types = ['text', 'animation',
                           'photo', 'video', 'video_note', 'voice']
            limits_dict = dict(zip(limit_types, limits[0]))

            if message_type not in limits_dict:
                logging.warning(f"Unknown message type: {message_type}")
                return False

            limit = limits_dict[message_type]

            if limit == 0:
                return False
            elif limit == -1:  # نامحدود
                return True
            else:
                # بررسی تعداد پیام‌ها در ساعت گذشته
                one_hour_ago = datetime.now() - timedelta(hours=1)
                query = '''
                    SELECT COUNT(*) 
                    FROM messages 
                    WHERE user_id = %s AND message_type = %s AND timestamp > %s
                '''
                result = execute_query(
                    conn, query, (user_id, message_type, one_hour_ago))

                if result:
                    count = result[0][0]
                    return count < limit
                else:
                    logging.error(
                        f"Failed to get message count for user {user_id}")
                    return False

        except Error as e:
            logging.error(f"Database error in check_message_limits: {e}")
            return False
        finally:
            conn.close()
    return False


async def update_message_count(user_id, message_type):
    conn = create_connection()
    if conn is not None:
        try:

            query = 'INSERT INTO messages (user_id, message_type, timestamp) VALUES (%s, %s, %s)'
            execute_query(conn, query, (user_id, message_type, datetime.now()))

        finally:
            conn.close()


############################################### Group Handler  ############################################

@dp.message(F.content_type.in_(['text', 'photo', 'animation', 'video', 'voice', 'video_note', 'video_chat_started']) & (F.chat.id == GROUP_ID))
async def message_handler(message: Message):
    user_id = message.from_user.id
    # group_id = message.chat.id
    # print(f'{group_id=}')

    if not await get_user_access_level(user_id):
        await message.reply(f'کاربر {user_id} قبل از ارسال پیام در گروه، با ربات زیر در چت خصوصی ثبت‌ نام کنید \n {BOT_USERNAME}')
        await message.delete()
        return

    message_type = message.content_type

    if not await check_message_limits(user_id, message_type):
        await message.reply(f'شما با آیدی {user_id} به محدودیت پیام‌های {message_type} خود رسیده‌اید.')
        await message.delete()
        return

    await update_message_count(user_id, message_type)


############################################# Bot Handler  ##########################################
async def is_member_group(group_id, user_id):
    return await bot.get_chat_member(group_id, user_id)


@dp.message((F.text.lower() == '/start') & (F.chat.type == 'private'))
async def command_start_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id in ADMINS_ID:
        keyboard = ReplyKeyboardBuilder()
        keyboard.add(
            KeyboardButton(text='ارتقاء کاربر'),
            KeyboardButton(text='اطلاعات کاربرها'),
            KeyboardButton(text='ارتقاء دسترسی'),
            KeyboardButton(text='پروفایل من')
        )
        keyboard.adjust(2)
        await message.reply('شما به عنوان مدیر می توانید از کلید زیر برای ارتقای دستی کاربران استفاده نمایید', reply_markup=keyboard.as_markup(resize_keyboard=True))
        return

    if await is_user_registered(message.from_user.id):
        keyboard = ReplyKeyboardBuilder()
        keyboard.add(
            KeyboardButton(text='ارتقاء دسترسی'),
            KeyboardButton(text='پروفایل من')
        )
        await message.reply('شما قبلا ثبت نام کرده اید \n . برای مشاهده پروفایل و ارتقاء سطح دسترسی از  کلیدهای زیر استفاده کنید', reply_markup=keyboard.as_markup(resize_keyboard=True))
    else:
        await state.set_state(Registration.twitter_id)
        await message.reply('کاربر گرامی خوش آمدید. \n برای ثبت نام آیدی تویتر خود با فرمت https://x.com/id   وارد کنید')


@dp.message(Registration.twitter_id)
async def process_twitter_id(message: types.Message, state: FSMContext):
    twitter_id = message.text.strip()

    if twitter_id.startswith('@') or not re.match(r'^https://x.com/[A-Za-z0-9_]{1,15}$', twitter_id):
        await message.reply("شناسه توییتر نامعتبر است. لطفاً یک شناسه توییتر معتبر با فرمت https://x.com/id  وارد کنید")
        return
        
    await state.update_data(twitter_id=message.text)
    await state.set_state(Registration.telegram_id)
    await message.answer('عالی. اکنون آیدی تلگرام خود را وارد کنید')


@dp.message(Registration.telegram_id)
async def process_telegram_id(message: types.Message, state: FSMContext):
    telegram_id = message.text.strip()

    # بررسی معتبر بودن Telegram ID (باید با @ شروع شود و تنها شامل کاراکترهای مجاز باشد)
    if not telegram_id.startswith('@') or not re.match(r'^@[A-Za-z0-9_]{5,32}$', telegram_id):
        await message.reply("شناسه تلگرام نامعتبر است. لطفاً یک شناسه معتبر که با @ شروع می شود وارد کنید")
        return
        
    await state.update_data(telegram_id=message.text)
    await state.set_state(Registration.age)
    await message.answer('ممنون. سن خود را وارد کنید')


@dp.message(Registration.age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        age= int(message.text)
        if not 10<age<100:
            await message.answer('لطفا عدد صحیح بین ۱۰ تا ۱۰۰ وارد کنید')
            return
            
    except ValueError:
        await message.answer('لطفا عدد صحیح وارد کنید')
        return
    
    await state.update_data(age=age)
    await state.set_state(Registration.city)
    await message.answer('ساکن کدام شهر هستید؟')


@dp.message(Registration.city)
async def process_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    await state.set_state(Registration.gender)
    await message.answer('خوب . جنسیت خود را وارد کنید')


@dp.message(Registration.gender)
async def process_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)
    await state.set_state(Registration.purpose)
    await message.answer('سوال آخر: هدف شما از پیوستن به گروه چیه?')


@dp.message(Registration.purpose)
async def process_purpose(message: types.Message, state: FSMContext):
    user_id = message.chat.id
    await state.update_data(purpose=message.text)
    data = await state.update_data(id=user_id)
    await state.clear()

    # Store user data in the database
    connection = create_connection()
    if connection:
        try:
            query = '''
            INSERT INTO users (id,twitter_id, telegram_id, age, city, gender, purpose)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            '''
            execute_query(connection, query, (
                data['id'],
                data['twitter_id'],
                data['telegram_id'],
                data['age'],
                data['city'],
                data['gender'],
                data['purpose']
            ))
        finally:
            connection.close()

    keyboard = ReplyKeyboardBuilder()
    keyboard.add(
        KeyboardButton(text='ارتقاء دسترسی'),
        KeyboardButton(text='پروفایل من')
    )

    await message.answer('ثبت نام شما انجام شد. \nدسترسی شما سطح ۱ می باشد برای ارتقاء  و مشاهده دسترسی خود کلیدهای زیر را انتخاب کنید ', reply_markup=keyboard.as_markup(resize_keyboard=True))
    
    is_member = await is_member_group(GROUP_ID, user_id)
    if is_member.status.value in ['kicked', 'left']:
        chat_invite_link=await message.bot.create_chat_invite_link(GROUP_ID,'عضویت در گروه',member_limit=1)
        await message.answer(f'برای عضویت در گروه بر روی لینک زیر کلیک کنید\n{chat_invite_link.invite_link}')
        return

@dp.message((F.text == 'ارتقاء کاربر') & (F.chat.type == 'private') & (F.chat.id.in_( ADMINS_ID)))
async def show_profile(message: types.Message, state: FSMContext):
    await state.set_state(AdminPromote.forward_id)
    await message.answer('لطفاً یک پیام از کاربر مورد نظر برای ارتقاء فوروارد کنید.')


@dp.message((F.text == 'اطلاعات کاربرها') & (F.chat.type == 'private') & (F.chat.id.in_(ADMINS_ID)))
async def show_profile(message: types.Message, state: FSMContext):
    file_path = await asyncio.to_thread(get_users_from_db)
    if file_path:
        file = FSInputFile(file_path)  # استفاده از FSInputFile برای آپلود فایل
        await message.reply_document(file)
        os.remove(file_path)  # حذف فایل پس از ارسال
    else:
        await message.reply("Failed to connect to the database.")


@dp.message(AdminPromote.forward_id)
async def handle_forward_id(message: Message, state: FSMContext):
    if message.forward_from:
        await state.update_data(forward_id=message.forward_from.id)
        levels = await get_access_levels()

        if not levels:
            await message.reply("در حال حاضر امکان ارتقا وجود ندارد. لطفاً بعداً تلاش کنید.")
            return

        keyboard = InlineKeyboardBuilder()

        for level in levels:
            button_text = f"سطح {level[0]}: {level[1]} تتر"
            keyboard.add(InlineKeyboardButton(
                text=button_text, callback_data=f"promote_{level[0]}"))

        await message.reply("دسترسی‌های زیر موجود است. لطفاً یک سطح را انتخاب کنید:", reply_markup=keyboard.adjust(1).as_markup())
        await state.set_state(AdminPromote.level)
    else:
        await state.clear()
        await message.reply("آیدی کاربر مخفی شده است ")        


@dp.callback_query(F.data.startswith('promote_'))
async def handle_level(callback_query: types.CallbackQuery, state: FSMContext):
    new_level = int(callback_query.data.split('_')[1])
    data = await state.update_data(level=new_level)
    await state.clear()

    connection = create_connection()
    if connection:
        try:
            update_query = "UPDATE users SET access_level = %s WHERE id = %s"
            execute_query(connection, update_query,
                          (data['level'], data['forward_id']))

            await callback_query.answer(f" سطح دسترسی کاربر {data['forward_id']} به {data['level']} ارتقا یافت!")
            await bot.send_message(int(GROUP_ID),f" سطح دسترسی کاربر {data['forward_id']} به {data['level']} ارتقا یافت!")

        finally:
            connection.close()


@dp.message((F.text == 'ارتقاء دسترسی') & (F.chat.type == 'private'))
async def upgrade_menu(message: types.Message):
    user_id = message.from_user.id
    current_level = await get_user_access_level(user_id)

    if current_level is None:
        await message.reply("شما هنوز ثبت‌نام نکرده‌اید. لطفاً ابتدا با استفاده از دستور /start ثبت‌نام کنید.")
        return

    levels = await get_access_levels()

    if not levels:
        await message.reply("در حال حاضر امکان ارتقا وجود ندارد. لطفاً بعداً تلاش کنید.")
        return

    keyboard = InlineKeyboardBuilder()

    for level in levels:
        if level[0] > current_level:
            button_text = f"سطح {level[0]}: {level[1]} تتر"
            keyboard.add(InlineKeyboardButton(
                text=button_text, callback_data=f"upgrade_{level[0]}"))

    await message.reply("دسترسی‌های زیر موجود است. لطفاً سطح مورد نظر خود را انتخاب کنید:", reply_markup=keyboard.adjust(1).as_markup())


@dp.callback_query(F.data.startswith('upgrade_'))
async def process_upgrade(callback_query: types.CallbackQuery, state: FSMContext):

    user_id = callback_query.from_user.id
    new_level = int(callback_query.data.split('_')[1])

    connection = create_connection()
    if connection:
        try:
            query = "SELECT price FROM levels WHERE level = %s"
            result = execute_query(connection, query, (new_level,))

            if not result:
                await callback_query.answer("خطا در دریافت اطلاعات سطح. لطفاً بعداً تلاش کنید.")
                return

            price = result[0][0]

            await bot.send_message(
                callback_query.from_user.id,
                f"برای ارتقا به سطح {new_level}، لطفاً {price} تتر به آدرس زیر واریز کنید:\n{
                    WALLET_ADDRESS}\nسپس هش تراکنش را ارسال کنید."
            )

            # ذخیره سطح جدید و قیمت در state
            await state.update_data(new_level=new_level, price=price)
            # تغییر وضعیت به دریافت هش تراکنش
            await state.set_state(Upgrade.txn_hash)

        finally:
            connection.close()


@dp.message(Upgrade.txn_hash)
async def receive_txn_hash(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    data = await state.get_data()
    new_level = data['new_level']
    price = data['price']
    txn_hash = message.text

    connection = create_connection()
    if connection:
        try:
            if await verify_transaction(txn_hash, price):
                txn_select_query = "SELECT user_id FROM transactions WHERE txn_hash = %s"
                result = execute_query(
                    connection, txn_select_query, (txn_hash,))
                if result:
                    await message.reply("این تراکنش قبلاً استفاده شده است.")
                else:
                    update_user_query = "UPDATE users SET access_level = %s WHERE id = %s"
                    execute_query(connection, update_user_query,
                                  (new_level, user_id))

                    txn_insert_query = "INSERT INTO transactions (user_id, level, txn_hash) VALUES (%s, %s, %s)"
                    execute_query(connection, txn_insert_query,
                                  (user_id, new_level, txn_hash))

                    await message.reply(f"پرداخت {price} تتر تایید شد. سطح دسترسی شما به {new_level} ارتقا یافت!")
            else:
                await message.reply("خطا در تأیید تراکنش. لطفاً دوباره تلاش کنید.")
        finally:
            connection.close()

        # پاک کردن state
        await state.clear()


async def verify_transaction(txn_hash, amount):
    amount = f"{int(float(amount) * 1e6)}"
    url = f"https://apilist.tronscan.org/api/transaction-info?hash={txn_hash}"
    async with AsyncSession() as session:
        response = await session.get(url, impersonate='chrome')
        if response.ok:
            data = response.json().get('tokenTransferInfo', None)
            if data and data['symbol'] == 'USDT' and data['to_address'] == WALLET_ADDRESS and data['amount_str'] == amount:
                return True

    return False


@dp.message((F.text == 'پروفایل من') & (F.chat.type == 'private'))
async def show_profile(message: types.Message):
    user_id = message.from_user.id
    connection = create_connection()
    if connection:
        try:
            query = 'SELECT access_level FROM users WHERE id = %s'
            result = execute_query(connection, query, (user_id,))

            if result:
                await message.reply(f'شما با آیدی {user_id} دسترسی سطح {result[0][0]} دارید')
            else:
                await message.reply('خطای دریافت اطلاعات پروفایل  . بعدا امتحان کنید')
        finally:
            connection.close()
    else:
        await message.reply('خطای ارتباط با دیتابیس . بعدا امتحان کنید')


async def main():
    await dp.start_polling(bot)

asyncio.run(main())
