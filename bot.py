import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F  # Для фильтров (если нужно)
from data_engine import MidiLog  # Ваш класс для работы с БД
import io
import os
import zipfile

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
API_TOKEN = os.environ.get("bot_token")  # Замените на свой токен
bot = Bot(token=API_TOKEN)
dp = Dispatcher()  # Диспетчер без аргументов в aiogram 3.x

# Создаем временную папку
if not os.path.exists('temp'):
    os.makedirs('temp')


# Клавиатура с кнопками
def get_period_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сегодня", callback_data="period_today"),
            InlineKeyboardButton(text="Неделя", callback_data="period_week"),
            InlineKeyboardButton(text="Всё время", callback_data="period_all"),
        ]
    ])
    return keyboard


# Обработчик команды /start
@dp.message(Command("start", "help"))
async def send_welcome(message: types.Message):
    await message.reply(
        "🎹 Бот для выгрузки MIDI-логов\nВыберите период:",
        reply_markup=get_period_keyboard()
    )


# Обработчик нажатий на Inline-кнопки
@dp.callback_query(F.data.startswith("period_"))
async def process_callback(callback_query: types.CallbackQuery):
    period = callback_query.data.split('_')[1]  # today, week или all
    days = {
        'today': 1,
        'week': 7,
        'all': 0
    }.get(period, 1)

    await callback_query.answer(f"Загружаю MIDI за {period}...")
    await send_midi_files(callback_query.message, days)


@dp.message(Command("play"))
async def handle_play_command(message: types.Message):
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.reply("ℹ️ Использование: /play <номер_сессии>")
        return

    ordered_num = int(args[1])
    result = MidiLog().play_midi(ordered_num)
    await message.reply(result)


# Общая функция для отправки MIDI
async def send_midi_files(message: types.Message, days: int, input_name: str = None):
    try:
        midi_sessions = MidiLog().get_midi_logs(days, input_name)

        if not midi_sessions:
            await message.reply("🚫 Нет данных за указанный период или устройство.", reply_markup=get_period_keyboard())
            return

        # Формируем текстовый список файлов
        file_list = "\n".join(
            f"{i + 1}. {name.replace('.mid', '')}"
            for i, (name, _) in enumerate(midi_sessions)
        )

        # Если сессия одна — отправляем файл
        if len(midi_sessions) == 1:
            session_name, midi_data = midi_sessions[0]
            await message.reply_document(
                document=types.BufferedInputFile(midi_data, filename=session_name),
                caption=f"🎵 MIDI-сессия: {file_list}"
            )
        else:
            # Создаем ZIP с нумерованными файлами
            zip_bytes = io.BytesIO()
            with zipfile.ZipFile(zip_bytes, 'w') as zipf:
                for i, (name, data) in enumerate(midi_sessions):
                    # Переименовываем файлы: "1_session_1_2023-10-25_14-30.mid"
                    new_name = f"{i + 1}_{name}"
                    zipf.writestr(new_name, data)

            await message.reply_document(
                document=types.BufferedInputFile(
                    file=zip_bytes.getvalue(),
                    filename=f"midi_sessions_{days}_days.zip"
                ),
                caption=f"📦 Файлы в архиве:\n{file_list}"
            )

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.reply("❌ Ошибка при загрузке MIDI.", reply_markup=get_period_keyboard())


# Команды /today, /week, /all
@dp.message(Command("today"))
async def today_midi(message: types.Message):
    await send_midi_files(message, 1)


@dp.message(Command("week"))
async def week_midi(message: types.Message):
    await send_midi_files(message, 7)


@dp.message(Command("all"))
async def all_midi(message: types.Message):
    await send_midi_files(message, 0)


# Запуск бота (для aiogram 3.x)
async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())