import io
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta

import rtmidi
from dateutil import parser
from mido import Message, MidiTrack
from mido import MidiFile

log = logging.getLogger()
log.addHandler(logging.StreamHandler())
log.setLevel(logging.DEBUG)


class MidiLog:
    DB_PATH = "data/midi_log.db"
    MAX_RETIRES = 3

    def __init__(self):
        self.retires = 0
        self.con = sqlite3.connect(self.DB_PATH)
        self.cur = self.con.cursor()
        self.cur.execute("""
                CREATE TABLE IF NOT EXISTS midi_log (
                    ID INTEGER PRIMARY KEY, 
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, 
                    input_name varchar(128), 
                    message_type varchar(128), 
                    message varchar(255))
            """)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cur.close()
        self.conn.close()
        super().__exit__()

    def refresh_cursor(self):
        log.warning('refresh connection')
        self.con = sqlite3.connect()
        self.cur = self.con.cursor()

    def retry(self, input_name, message):
        self.retires += 1
        if self.retires == self.MAX_RETIRES:
            time.sleep(1)
            self.retires = 0
            self.retry(input_name, message)
        try:
            self.refresh_cursor()
            self.add_messages(input_name, message)
        except Exception as e:
            log.error(e)
            log.error(e)
            time.sleep(.01)
            self.retry()

    def add_messages(self, input_name, message):
        timestamp = datetime.utcnow()
        data = [(
            timestamp,
            input_name,
            message.type,
            json.dumps(message.dict())
        )]
        log.debug("add message %s" % str(data))

        try:
            self.cur.executemany(
                "INSERT INTO midi_log VALUES(NULL, ?, ?, ?, ?)", data
            )
            self.con.commit()
        except Exception as e:
            log.exception(e)
            self.retry(input_name, message)
        else:
            self.retires = 0

    def get_midi_logs(self, days: int, input_name: str = None) -> list[tuple[str, bytes, int, str, str]]:
        """
        Генерирует MIDI-файлы и возвращает:
        - имя файла
        - данные в bytes
        - количество нот
        - отформатированную дату (дд.мм.гггг)
        - отформатированное время (чч:мм)
        """
        try:
            # 1. Запрос к БД с фильтрами
            query = """
                SELECT timestamp, message 
                FROM midi_log 
                WHERE timestamp >= ? 
                {}  -- Фильтр по устройству
                ORDER BY timestamp
            """
            conditions = []
            params = []

            if days > 0:
                cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
                params.append(cutoff_date)
            else:
                params.append("1970-01-01")  # Все записи

            if input_name:
                conditions.append("AND input_name = ?")
                params.append(input_name)

            query = query.format(" ".join(conditions))
            self.cur.execute(query, params)
            records = self.cur.fetchall()

            if not records:
                return []

            # 2. Группировка по сессиям (интервал >=1 минуты = новая сессия)
            sessions = {}  # Инициализируем словарь сессий
            session_id = 0
            prev_timestamp = parser.parse(records[0][0])

            for row in records:
                current_timestamp = parser.parse(row[0])
                if (current_timestamp - prev_timestamp) >= timedelta(minutes=1):
                    session_id += 1
                if session_id not in sessions:
                    sessions[session_id] = {
                        "start_time": current_timestamp,
                        "messages": []
                    }
                sessions[session_id]["messages"].append((current_timestamp, json.loads(row[1])))
                prev_timestamp = current_timestamp

            # 3. Создание MIDI-файлов с учетом времени
            result = []
            for session_id, data in sessions.items():
                midi_file = MidiFile()
                track = MidiTrack()
                midi_file.tracks.append(track)

                prev_time = data["start_time"]
                notes_count = 0  # Счетчик нот

                for timestamp, msg_dict in data["messages"]:
                    delta_ticks = int((timestamp - prev_time).total_seconds() * 1000)
                    msg = Message.from_dict(msg_dict)
                    msg.time = delta_ticks
                    track.append(msg)
                    prev_time = timestamp

                    # Подсчет нот
                    if msg.type == 'note_on':
                        notes_count += 1

                # Сохраняем в bytes
                midi_bytes = io.BytesIO()
                midi_file.save(file=midi_bytes)

                # Форматируем дату и время
                device_tag = f"_{input_name}" if input_name else ""
                start_time = data['start_time']
                session_name = f"session_{session_id}{device_tag}_{start_time.strftime('%Y-%m-%d_%H-%M')}.mid"
                formatted_date = start_time.strftime("%d.%m.%Y")
                formatted_time = start_time.strftime("%H:%M")

                result.append((
                    session_name,
                    midi_bytes.getvalue(),
                    notes_count,
                    formatted_date,
                    formatted_time
                ))

            return result

        except Exception as e:
            logging.error(f"Error in get_midi_logs: {e}")
            return []

    def play_midi(self, ordered_num: int, output_device: str = None) -> str:
        """
        Воспроизводит MIDI-файл по номеру сессии
        :param ordered_num: Номер сессии в списке
        :param output_device: Опциональное имя устройства вывода
        :return: Статус воспроизведения
        """
        try:
            # 1. Получаем MIDI-файл из БД
            query = """
                SELECT timestamp, message 
                FROM midi_log 
                WHERE session_id = (
                    SELECT session_id 
                    FROM (
                        SELECT DISTINCT session_id 
                        FROM midi_log 
                        ORDER BY MIN(timestamp)
                    ) 
                    LIMIT 1 OFFSET ?
                )
                ORDER BY timestamp
            """
            self.cur.execute(query, (ordered_num - 1,))
            records = self.cur.fetchall()

            if not records:
                return f"🚫 Сессия №{ordered_num} не найдена"

            # 2. Создаем MIDI-файл в памяти
            midi_file = MidiFile()
            track = MidiTrack()
            midi_file.tracks.append(track)

            prev_time = parser.parse(records[0][0])
            for row in records:
                current_time = parser.parse(row[0])
                delta = int((current_time - prev_time).total_seconds() * 1000)
                msg = Message.from_dict(json.loads(row[1]))
                msg.time = delta
                track.append(msg)
                prev_time = current_time

            # 3. Воспроизведение с выбором устройства
            try:
                import rtmidi

                midi_out = rtmidi.MidiOut()
                available_ports = midi_out.get_ports()

                if output_device:
                    if output_device not in available_ports:
                        return f"🚫 Устройство '{output_device}' не найдено. Доступные: {', '.join(available_ports)}"

                    port_index = available_ports.index(output_device)
                    midi_out.open_port(port_index)
                    device_info = output_device
                else:
                    if available_ports:
                        midi_out.open_port(0)
                        device_info = available_ports[0]
                    else:
                        midi_out.open_virtual_port("Virtual Output")
                        device_info = "виртуальное устройство"

                # 4. Воспроизведение в отдельном потоке
                def play_thread():
                    try:
                        for msg in midi_file:
                            if msg.type in ['note_on', 'note_off', 'control_change']:
                                midi_out.send_message(msg.bytes())
                            time.sleep(msg.time / 1000)  # Преобразуем ms в секунды
                    finally:
                        midi_out.close_port()
                        del midi_out

                import threading
                threading.Thread(target=play_thread, daemon=True).start()

                return f"🎵 Воспроизводится сессия №{ordered_num} на устройстве: {device_info}"

            except ImportError:
                # Fallback для систем без rtmidi
                import pygame.midi
                pygame.midi.init()

                device_id = 0
                if output_device:
                    for i in range(pygame.midi.get_count()):
                        info = pygame.midi.get_device_info(i)
                        if info[1].decode() == output_device and info[2] == 0:
                            device_id = i
                            break

                player = pygame.midi.Output(device_id)
                for msg in midi_file:
                    if msg.type in ['note_on', 'note_off']:
                        player.write_short(msg.bytes())
                    time.sleep(msg.time / 1000)
                player.close()

                return f"🎵 Воспроизведено сессия №{ordered_num} (через pygame.midi)"

        except Exception as e:
            logging.error(f"Ошибка воспроизведения MIDI: {e}")
            return f"❌ Ошибка воспроизведения сессии №{ordered_num}"

    def get_session_by_id(self, session_id: int, input_name: str = None):
        """
        Возвращает данные конкретной сессии по её номеру
        :param session_id: Номер сессии (начиная с 0)
        :param input_name: Фильтр по устройству (опционально)
        :return: Кортеж с данными сессии или None если не найдена
        """
        try:
            all_sessions = self.get_midi_logs(days=0, input_name=input_name)
            if session_id < 0 or session_id >= len(all_sessions):
                return None
            return all_sessions[session_id]
        except Exception as e:
            logging.error(f"Error in get_session_by_id: {e}")
            return None

    async def send_midi_visualization(self, chat_id: int, session_id: int, input_name: str = None):
        """
        Генерирует и отправляет визуализацию нот для выбранной сессии в телеграм
        :param chat_id: ID чата для отправки
        :param session_id: Номер сессии
        :param input_name: Фильтр по устройству (опционально)
        """
        try:
            # Получаем данные сессии
            session_data = self.get_session_by_id(session_id, input_name)
            if not session_data:
                await self.bot.send_message(chat_id, "Сессия не найдена")
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
                    await self.bot.send_message(chat_id, "В сессии не найдено нот")
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
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=plot_buffer,
                        caption=f"Сессия {session_id} | {session_data[3]} {session_data[4]}\n"
                                f"Всего нот: {session_data[2]}"
                    )

                plt.close()

        except Exception as e:
            logging.error(f"Error in send_midi_visualization: {e}")
            await self.bot.send_message(chat_id, "Произошла ошибка при создании визуализации")