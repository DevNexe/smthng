# Mic USB Bridge protocol

Направление: **ПК (отправитель) → телефон (приёмник)**. Телефон слушает UDP-порт, ПК шлёт пакеты на IP телефона в USB-сети.

Каждый UDP datagram имеет big-endian заголовок 12 байт и PCM payload.

| Смещение | Размер | Поле |
|---:|---:|---|
| 0 | 4 | magic `0x4D555331` (`MUS1`) |
| 4 | 4 | unsigned sequence number |
| 8 | 4 | количество PCM samples в payload |
| 12 | N | signed **little-endian** PCM16 mono samples |

Параметры потока: 48,000 Hz, 16 bit, 1 канал, 480 samples/frame (10 ms). Приёмник проверяет magic и размер, считает потери по sequence и пишет payload в `AudioTrack`.
