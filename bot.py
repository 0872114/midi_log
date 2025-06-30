from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import io
import logging
import os
import zipfile
from collections import defaultdict
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import List
import matplotlib.pyplot as plt

import mido
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import Message
from mido import MidiFile

from data_engine import MidiLog

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Временная папка для хранения MIDI-файлов
TEMP_DIR = "temp_midi"
os.makedirs(TEMP_DIR, exist_ok=True)

# Глобальные переменные для хранения состояния
active_ports = {}
output_devices_cache = []

# Инициализация бота
API_TOKEN = os.environ.get("bot_token")
if not API_TOKEN:
    logger.error("Не задан токен бота!")
    raise ValueError("Токен бота не найден в переменных окружения")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Инициализация подключения к БД
try:
    db = MidiLog()
    logger.info("Успешное подключение к БД")
except Exception as e:
    logger.error(f"Ошибка подключения к БД: {e}")
    raise


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
    try:
        await message.reply(
            "🎹 Бот для выгрузки MIDI-логов\nВыберите период:",
            reply_markup=get_period_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка в send_welcome: {e}")
        with suppress(Exception):
            await message.reply("⚠️ Произошла ошибка. Попробуйте позже.")


@dp.callback_query(F.data.startswith("period_"))
async def process_callback(callback_query: types.CallbackQuery):
    try:
        if not callback_query.message:
            logger.warning("Callback без message!")
            return

        period = callback_query.data.split('_')[1]  # today, week или all
        days = {
            'today': 1,
            'week': 7,
            'all': 0
        }.get(period, 1)

        await callback_query.answer(f"Загружаю MIDI за {period}...")
        await send_midi_files(callback_query.message, days)
    except Exception as e:
        logger.error(f"Ошибка в process_callback: {e}")
        with suppress(Exception):
            await callback_query.answer("⚠️ Ошибка при обработке запроса")


def format_notes_count(notes: int) -> str:
    """Форматирование количества нот с правильным склонением"""
    if not isinstance(notes, int):
        return "н/д нот"

    last_digit = notes % 10
    last_two_digits = notes % 100

    if 11 <= last_two_digits <= 19:
        word = "нот"
    elif last_digit == 1:
        word = "нота"
    elif 2 <= last_digit <= 4:
        word = "ноты"
    else:
        word = "нот"

    # Форматирование с разделителем тысяч
    formatted_num = "{:,}".format(notes).replace(",", " ")
    return f"{formatted_num} {word}"


async def send_midi_files(message: types.Message, days: int, input_name: str = None):
    try:
        # Теперь получаем кортеж (name, data, notes_count, formatted_date, formatted_time)
        midi_sessions = db.get_midi_logs(days, input_name)

        if not midi_sessions:
            await message.reply("🚫 Нет данных за указанный период или устройство.",
                                reply_markup=get_period_keyboard())
            return

        # Группируем по датам и считаем общее количество нот
        date_sessions = defaultdict(list)
        total_notes = sum(session[2] for session in midi_sessions)

        for name, data, notes_count, formatted_date, formatted_time in midi_sessions:
            date_sessions[formatted_date].append({
                'time': formatted_time,
                'notes': notes_count,
                'name': name,
                'data': data
            })

        # Сортируем сессии по времени внутри дат
        for date in date_sessions:
            date_sessions[date].sort(key=lambda x: x['time'])

        # Формируем список файлов
        file_list = []
        current_number = 1

        for date, sessions in sorted(date_sessions.items(),
                                     key=lambda x: datetime.strptime(x[0], "%d.%m.%Y"),
                                     reverse=True):
            file_list.append(f"\n📅 {date}:")
            for session in sessions:
                notes_text = format_notes_count(session['notes'])
                file_list.append(f"  {current_number}. Сессия {session['time']} ({notes_text})")
                current_number += 1

        file_list_text = "\n".join(file_list)
        total_notes_text = format_notes_count(total_notes)

        # Отправка результата
        if len(midi_sessions) == 1:
            session = midi_sessions[0]
            await message.reply_document(
                document=types.BufferedInputFile(session[1], filename=session[0]),
                caption=f"🎵 MIDI-сессия: {file_list_text}"
            )
        else:
            with io.BytesIO() as zip_bytes:
                with zipfile.ZipFile(zip_bytes, 'w') as zipf:
                    for session in midi_sessions:
                        zipf.writestr(session[0], session[1])

                zip_data = zip_bytes.getvalue()
                if len(zip_data) > 50 * 1024 * 1024:
                    await message.reply("⚠️ Архив слишком большой для отправки")
                    return

                await message.reply_document(
                    document=types.BufferedInputFile(
                        file=zip_data,
                        filename=f"midi_sessions_{days}_days.zip"
                    ),
                    caption=f"📦 Файлы в архиве:\n{file_list_text}"
                )

    except Exception as e:
        logger.error(f"Ошибка в send_midi_files: {e}")
        await message.reply("❌ Ошибка при загрузке MIDI.", reply_markup=get_period_keyboard())


def safe_filename(filename: str) -> str:
    """Очистка имени файла от небезопасных символов"""
    keepchars = (' ', '.', '_', '-')
    return "".join(c for c in filename if c.isalnum() or c in keepchars).rstrip()


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


# Плеер
def get_output_names() -> List[str]:
    """Получаем список доступных MIDI-устройств"""
    try:
        return mido.get_output_names()
    except Exception as e:
        logger.error(f"Error getting MIDI devices: {e}")
        return []


@dp.message(Command("devices"))
async def list_devices(message: types.Message):
    """Обработчик команды /devices"""
    try:
        output_devices = get_output_names()
        if not output_devices:
            await message.reply("🔴 MIDI-устройства не обнаружены")
            return

        response = ["🎵 Доступные MIDI-устройства:"]
        for i, device in enumerate(output_devices, 1):
            response.append(f"{i}. {device}")

        response.append("\nℹ️ Для воспроизведения используйте /play <номер_устройства>")
        await message.reply("\n".join(response))

        # Обновляем кеш устройств
        global output_devices_cache
        output_devices_cache = output_devices
    except Exception as e:
        logger.error(f"Ошибка в list_devices: {e}")
        await message.reply("⚠️ Ошибка при получении списка устройств")


@dp.message(Command("play"))
async def play_handler(message: Message):
    """Обработчик команды /play"""
    try:
        args = message.text.split()[1:]
        if len(args) < 1:
            await message.reply("ℹ️ Используйте: /play <номер_устройства>")
            return

        device_num = int(args[0])
        output_devices = get_output_names()

        if not output_devices:
            await message.reply("🔴 MIDI-устройства не обнаружены. Используйте /devices для проверки.")
            return

        if device_num < 1 or device_num > len(output_devices):
            await message.reply(f"⚠️ Неверный номер устройства. Доступно устройств: {len(output_devices)}")
            return

        port_name = output_devices[device_num - 1]

        # Проверяем, есть ли сохраненный файл для пользователя
        user_file = Path(TEMP_DIR) / f"{message.from_user.id}.mid"
        if not user_file.exists():
            await message.reply("ℹ️ Сначала отправьте мне MIDI-файл.")
            return

        await message.reply(f"🔄 Воспроизведение на устройстве {port_name}...")

        # Воспроизводим файл
        if play_midi_file(port_name, str(user_file)):
            await message.reply("✅ Воспроизведение завершено")
        else:
            await message.reply("⚠️ Ошибка при воспроизведении MIDI-файла")
    except ValueError:
        await message.reply("⚠️ Номер устройства должен быть числом")
    except Exception as e:
        logger.error(f"Ошибка в play_handler: {e}")
        await message.reply("⚠️ Произошла ошибка при воспроизведении")


@dp.message(F.document)
async def handle_midi_file(message: Message):
    """Обработчик получения MIDI-файла"""
    try:
        if message.document.mime_type != "audio/midi":
            await message.reply("⚠️ Пожалуйста, отправьте файл в формате MIDI (.mid)")
            return

        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path

        # Сохраняем файл
        user_file = Path(TEMP_DIR) / f"{message.from_user.id}.mid"
        await bot.download_file(file_path, destination=user_file)

        await message.reply(
            "🎶 MIDI-файл успешно сохранен!\n"
            "Используйте /devices для просмотра доступных устройств\n"
            "и /play <номер_устройства> для воспроизведения."
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке MIDI-файла: {e}")
        await message.reply("⚠️ Произошла ошибка при обработке файла")


@dp.message(Command("notes"))
async def handle_visualize(message: types.Message):
    try:
        # Получаем аргументы команды (все что после /notes)
        command_args = message.text.split()[1:] if message.text else []

        session_id = int(command_args[0]) if len(command_args) > 0 else 0
        input_name = command_args[1] if len(command_args) > 1 else None

        # Получаем данные сессии
        session_data = db.get_session_by_id(session_id, input_name)
        if not session_data:
            await message.reply("Сессия не найдена")
            return

        # Извлекаем MIDI данные
        midi_bytes = session_data[1]

        # Создаем временный файл для анализа
        with io.BytesIO(midi_bytes) as midi_stream:
            midi_file = MidiFile(file=midi_stream)

            # Анализируем треки и собираем данные для графика
            notes = []
            times = []

            for i, track in enumerate(midi_file.tracks):
                current_time = 0
                for msg in track:
                    current_time += msg.time
                    if msg.type == 'note_on' and msg.velocity > 0:
                        notes.append(msg.note)
                        times.append(current_time / 1000)  # переводим в секунды

            if not notes:
                await message.reply("В сессии не найдено нот")
                return

            # Создаем график
            plt.figure(figsize=(10, 5))
            plt.scatter(times, notes, alpha=0.5)
            plt.title(f"Визуализация нот сессии {session_id}")
            plt.xlabel("Время (секунды)")
            plt.ylabel("Высота ноты")
            plt.grid(True)

            # Сохраняем во временный файл
            with io.BytesIO() as plot_buffer:
                plt.savefig(plot_buffer, format='png')
                plot_buffer.seek(0)

                # Отправляем изображение
                await message.reply_photo(
                    photo=types.BufferedInputFile(
                        file=plot_buffer.getvalue(),
                        filename=f"notes_visualization_{session_id}.png"
                    ),
                    caption=f"Сессия {session_id} | {session_data[3]} {session_data[4]}\n"
                            f"Всего нот: {session_data[2]}"
                )

            plt.close()

    except (IndexError, ValueError):
        await message.reply("Использование: /notes [номер_сессии] [устройство]")
    except Exception as e:
        logging.error(f"Error in handle_visualize: {e}")
        await message.reply("Произошла ошибка при создании визуализации")


# Добавляем недостающую функцию для воспроизведения MIDI
def play_midi_file(port_name: str, file_path: str) -> bool:
    """Воспроизведение MIDI-файла на указанном устройстве"""
    try:
        with mido.open_output(port_name) as port:
            midi_file = MidiFile(file_path)
            for msg in midi_file.play():
                port.send(msg)
        return True
    except Exception as e:
        logging.error(f"Error in play_midi_file: {e}")
        return False

# Запуск бота
async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    import asyncio

    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Фатальная ошибка: {e}")
    finally:
        # Закрытие соединения с БД
        with suppress(Exception):
            db.close()
