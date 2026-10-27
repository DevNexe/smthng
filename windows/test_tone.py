"""Тест сети без захвата звука: шлёт на телефон тон 440 Гц.
Запуск: py test_tone.py <IP телефона> [порт]
Телефон: приложение «Mic USB Receiver» -> «НАЧАТЬ ПРИЁМ» (громкость медиа не на нуле)."""
import math
import socket
import struct
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.42.129"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 42042
MAGIC, RATE, FRAME = 0x4D555331, 48000, 480

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
seq = phase = 0
next_t = time.perf_counter()
last_print = time.time()
print(f"Шлю тон 440 Гц на {HOST}:{PORT}  (Ctrl+C — стоп)")
while True:
    samples = [int(12000 * math.sin(2 * math.pi * 440 * (phase + i) / RATE)) for i in range(FRAME)]
    phase += FRAME
    sock.sendto(struct.pack(">III", MAGIC, seq, FRAME) + struct.pack("<%dh" % FRAME, *samples), (HOST, PORT))
    seq += 1
    next_t += FRAME / RATE
    while time.perf_counter() < next_t:
        pass
    if time.time() - last_print >= 1:
        last_print = time.time()
        print(f"отправлено пакетов: {seq}")
