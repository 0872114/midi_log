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

    def get_midi_logs(self, days: int, input_name: str = None) -> list[tuple[str, bytes]]:
        """
        Генерирует MIDI-файлы с правильными временными интервалами между сообщениями.
        Возвращает список кортежей (имя_файла, данные_в_bytes).
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
            sessions = {}
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
                for timestamp, msg_dict in data["messages"]:
                    # Рассчитываем дельта-время в тиках (1 секунда = 1000 ms)
                    delta_ticks = int((timestamp - prev_time).total_seconds() * 1000)
                    msg = Message.from_dict(msg_dict)
                    msg.time = delta_ticks  # Устанавливаем время задержки
                    track.append(msg)
                    prev_time = timestamp

                # Сохраняем в bytes
                midi_bytes = io.BytesIO()
                midi_file.save(file=midi_bytes)
                device_tag = f"_{input_name}" if input_name else ""
                session_name = f"session_{session_id}{device_tag}_{data['start_time'].strftime('%Y-%m-%d_%H-%M')}.mid"
                result.append((session_name, midi_bytes.getvalue()))

            return result

        except Exception as e:
            logging.error(f"Error in get_midi_logs: {e}")
            return []

    def play_midi(self, ordered_num: int) -> str:
        """
        Проигрывает MIDI-файл на подключенных внешних устройствах.
        :param ordered_num: Номер сессии из списка (начиная с 1).
        :return: Статус выполнения (успех/ошибка).
        """
        try:
            # 1. Получаем список сессий
            midi_sessions = self.get_midi_logs(days=0)  # Берём все сессии

            if not midi_sessions:
                return "❌ Нет доступных MIDI-сессий."

            if ordered_num < 1 or ordered_num > len(midi_sessions):
                return f"❌ Неверный номер сессии. Допустимый диапазон: 1-{len(midi_sessions)}."

            # 2. Извлекаем выбранную сессию
            session_name, midi_data = midi_sessions[ordered_num - 1]

            # 3. Создаем временный MIDI-файл
            temp_file = f"temp/temp_session_{ordered_num}.mid"
            with open(temp_file, 'wb') as f:
                f.write(midi_data)

            # 4. Проигрываем MIDI на устройствах
            midi_out = rtmidi.MidiOut()
            available_ports = midi_out.get_ports()

            if not available_ports:
                return "❌ Нет подключенных MIDI-устройств."

            # Выбираем первое доступное устройство (можно добавить выбор)
            midi_out.open_port(0)

            # 5. Воспроизводим MIDI-файл
            mid = MidiFile(temp_file)
            for msg in mid.play():
                midi_out.send_message(msg.bytes())

            # 6. Очистка
            midi_out.close_port()
            del midi_out
            os.remove(temp_file)

            return f"✅ Успешно проиграна сессия: {session_name}"

        except Exception as e:
            logging.error(f"Ошибка в play_midi: {e}")
            return f"❌ Ошибка при воспроизведении: {str(e)}"
