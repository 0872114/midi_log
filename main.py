import mido
import time
from db import MidiLog
import logging
import threading


log = logging.getLogger()
log.addHandler(logging.StreamHandler())
log.setLevel(logging.DEBUG)


class MidiLogApp:
    def __init__(self):
        self.midi_log = MidiLog()
        self.pause = False

    def add_messages(self):
        midi_log = MidiLog()
        input_names = set(mido.get_input_names())
        open_ports = [mido.open_input(input_name) for input_name in input_names]
        for port in open_ports:
            if port.closed:
                return True
            for msg in port:
                midi_log.add_messages(port.name, msg)
            port.close()
            return

    def process(self):

        input_names = set(mido.get_input_names())
        open_ports = [mido.open_input(input_name) for input_name in input_names]
        i = 0
        while True:
            i += 1
            if i >= 100000:
                print('recon')
                i = 0
                for port in open_ports:
                    port.close()

                input_names = set(mido.get_input_names())
                try:
                    open_ports = [mido.open_input(input_name) for input_name in input_names]
                except Exception:
                    print('no port')
                    time.sleep(1)
            a = ([(port.closed) for port in open_ports])
            time.sleep(.00001)
            for port, msg in mido.ports.multi_receive(ports=open_ports, yield_ports=True, block=False):
                if port.closed:
                    print('pori clo')
                    continue
                self.midi_log.add_messages(str(port), msg)


if __name__ == "__main__":
    app = MidiLogApp()
    app.process()
