import sqlite3
import os
import time
from datetime import datetime
import json
import logging


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
