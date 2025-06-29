from db import MidiLog
from datetime import datetime, timedelta
from dateutil import parser


if __name__ == "__main__":
    midi_log = MidiLog()
    res = midi_log.cur.execute("select * from midi_log order by timestamp")
    sessions = {}
    amount = 0
    prev_timestamp = datetime.now()
    for row in res.fetchall():
        if parser.parse(row[1]) - prev_timestamp >= timedelta(minutes=1):
            amount += 1
            sessions[amount] = row[1]
        prev_timestamp =parser.parse(row[1])
    for key, val in sessions.items():
        print(f"{key}: {val}")
