import sys
import time
import serial
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit
from PyQt6.QtCore import QThread, pyqtSignal
import pyqtgraph as pg
import config
import numpy as np


PORT = "/dev/cu.usbmodem3277354534391"
BAUD = 115200
FRAME_WORDS = 3694
FRAME_SIZE = FRAME_WORDS * 2
MARKER = [157, 58, 71]


def bytes_to_uint16(data, offset=0):
    return data[offset] | (data[offset + 1] << 8)


def find_marker(data):
    for i in range(0, len(data) - len(MARKER) * 2, 2):
        nums = [bytes_to_uint16(data, i + j * 2) for j in range(len(MARKER))]
        if nums == MARKER:
            return i
    return None


def parse_frame(data):
    return [bytes_to_uint16(data, i) for i in range(0, len(data), 2)]


def send_command(ser, streaming: bool):
    # byte[1-2]: The characters E and R. Defines where the firmware should start reading in its circular input-buffer.
    # byte[3-6]: The 4 bytes constituting the 32-bit int holding the SH-period
    # byte[7-10]: The 4 bytes constituting the 32-bit int holding the ICG-period
    # byte[11]: Continuous flag: 0 equals one acquisition, 1 equals continuous mode
    # byte[12]: The number of integrations to average
    config.txfull[0] = 69
    config.txfull[1] = 82
    config.txfull[2] = (config.SHperiod >> 24) & 0xff
    config.txfull[3] = (config.SHperiod >> 16) & 0xff
    config.txfull[4] = (config.SHperiod >> 8) & 0xff
    config.txfull[5] = config.SHperiod & 0xff
    config.txfull[6] = (config.ICGperiod >> 24) & 0xff
    config.txfull[7] = (config.ICGperiod >> 16) & 0xff
    config.txfull[8] = (config.ICGperiod >> 8) & 0xff
    config.txfull[9] = config.ICGperiod & 0xff
    config.txfull[10] = 1 if streaming else 0
    config.txfull[11] = config.AVGn[1]

    ser.write(config.txfull)
    print(f"tx_data: {config.txfull}")


class ReaderThread(QThread):
    frame_received = pyqtSignal(list)

    def __init__(self, port, baud, streaming=False):
        super().__init__()
        self.port = port
        self.baud = baud
        self.streaming = streaming
        self.ser = None
        self.running = False

    def run(self):
        self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
        self.running = True

        buffer = bytearray()
        send_command(self.ser, streaming=self.streaming)

        while self.running:
            try:
                data = self.ser.read(2048)
            except serial.SerialException:
                # Port likely closed/unavailable; exit gracefully
                break
            if data:
                buffer.extend(data)

            pos = find_marker(buffer)
            if pos is not None and len(buffer) >= pos + FRAME_SIZE:
                frame_bytes = buffer[pos:pos + FRAME_SIZE]
                numbers = parse_frame(frame_bytes)

                self.frame_received.emit(numbers)

                buffer = buffer[pos + FRAME_SIZE:]

                if not self.streaming:
                    # якщо режим одного кадру — завершуємось
                    self.running = False
                    break
        # Cleanup on exit
        self.request_stop()

    def request_stop(self):
        # Signal loop to stop; if streaming, ask device to stop sending
        self.running = False
        try:
            if self.ser and self.ser.is_open:
                if self.streaming:
                    send_command(self.ser, streaming=False)
                    while self.ser.out_waiting > 0:
                        time.sleep(0.1)
                    # give the device a moment to process the stop
                    time.sleep(0.1)
                self.ser.close()
        except Exception:
            # Ignore errors during shutdown
            pass


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UART Reader")
        self.resize(800, 600)

        layout = QVBoxLayout()

        self.one_frame_btn = QPushButton("One Frame")
        self.start_btn = QPushButton("Start Streaming")
        self.stop_btn = QPushButton("Stop")
        self.log = QTextEdit()
        self.log.setReadOnly(True)

        self.plot = pg.PlotWidget(title="Sensor Data")
        self.curve = self.plot.plot(pen='g')
        self.plot.setLabel('bottom', 'Pixel number')


        layout.addWidget(self.one_frame_btn)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(self.plot)
        layout.addWidget(self.log)
        self.setLayout(layout)

        self.reader_thread = None

        self.one_frame_btn.clicked.connect(self.get_one_frame)
        self.start_btn.clicked.connect(self.start_streaming)
        self.stop_btn.clicked.connect(self.stop_reading)

    def get_one_frame(self):
        if self.reader_thread and self.reader_thread.isRunning():
            return
        self.reader_thread = ReaderThread(PORT, BAUD, streaming=False)
        self.reader_thread.frame_received.connect(self.on_frame)
        self.reader_thread.start()
        self.log.append("📸 Запит одного кадру...")
        self.stop_reading()

    def start_streaming(self):
        if self.reader_thread and self.reader_thread.isRunning():
            return
        self.reader_thread = ReaderThread(PORT, BAUD, streaming=True)
        self.reader_thread.frame_received.connect(self.on_frame)
        self.reader_thread.start()
        self.log.append("▶️ Стрімінг запущено")

    def stop_reading(self):
        if self.reader_thread and self.reader_thread.ser:
            try:
                # Ask the reader thread to stop safely; do not close the port here
                self.reader_thread.request_stop()
                self.log.append("🛑 Надіслано команду зупинки")
            except Exception as e:
                self.log.append(f"⚠️ Помилка при зупинці: {e}")

        if self.reader_thread:
            self.reader_thread.wait()
            self.reader_thread = None
            self.log.append("⏹ Зупинено")

    def on_frame(self, numbers):
        self.log.append(f"✅ Кадр отримано (перші числа {numbers[:5]})")
        y = np.asarray(numbers, dtype=np.uint16)
        if config.datainvert == 1:
            # plot intensities
            # Convert to numpy for vectorized math using a signed dtype to avoid underflow
            config.pltData16 = (y[10] + y[11]) / 2 - y
            # This subtracts the average difference between even and odd pixels from the even pixels
            if config.balanced == 1:
                config.offset = (config.pltData16[18] + config.pltData16[20] + config.pltData16[22] + config.pltData16[24] -
                                 config.pltData16[19] - config.pltData16[21] - config.pltData16[23] - config.pltData16[25]) / 4
                for i in range(1847):
                    config.pltData16[2 * i] = config.pltData16[2 * i] - config.offset
            self.plot.setLabel('left', 'Intensity')
            self.plot.setXRange(0, 3694)
            self.plot.setYRange(0, 3250)
            # self.plot.enableAutoRange(x=False, y=True)
        else:
            # plot raw data
            self.plot.setLabel('left', 'ADCcount')
            self.plot.setXRange(0, 3694)
            self.plot.setYRange(0, 4095)
            # self.plot.enableAutoRange(x=False, y=True)

        x = list(range(len(y)))
        self.curve.setData(x, y)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
