# RS232 Dual-Lane Logger & Injector

Ein Raspberry Pi Pico liest zwei RS232-Leitungen gleichzeitig mit (Logger)
und kann auf Wunsch selbst Daten auf jede der beiden Leitungen schreiben
(Injector). Ein Python-Desktop-Client zeigt den Live-Traffic beider Lanes
an, erlaubt das Senden von Kommandos und das Speichern der Logs.

```
[RS232 Gerät 1] --TX/RX--> [MAX3232] --UART0--> [Pico] --USB--> [PC / Desktop-Client]
[RS232 Gerät 2] --TX/RX--> [MAX3232] --UART1--> [Pico]
```

## Projektstruktur

```
.
├── pico/
│   └── main.py          # MicroPython-Firmware für den Pico
├── client/
│   ├── rs232_client.py  # Desktop-GUI-Client (Tkinter)
│   └── requirements.txt
└── README.md
```

## Hardware-Aufbau

- Raspberry Pi Pico (kein "W" nötig – die Kommunikation läuft über USB, nicht WLAN)
- 2x MAX3232 (oder vergleichbarer RS232-TTL-Pegelwandler) zwischen den
  echten RS232-Leitungen und den Pico-GPIOs
- Jumper-Konzept: pro physischer RS232-Leitung entscheidet ein Jumper,
  ob der Pico als Empfänger (Log) oder Sender (Inject) an dieser Leitung hängt

| Lane | UART | TX-Pin | RX-Pin | Hinweis |
|------|------|--------|--------|---------|
| Lane 1 | UART0 | GP0 | GP1 | über MAX3232 |
| Lane 2 | UART1 | GP4 | GP5 | über MAX3232 |

**Wichtig:** RS232-Pegel (±5…15V) niemals direkt an die GPIOs anschließen –
immer über einen Pegelwandler (MAX3232 o.ä.), sonst wird der Pico beschädigt.

## Installation – Pico-Firmware

1. [MicroPython-Firmware](https://micropython.org/download/RPI_PICO/) auf
   den Pico flashen (BOOTSEL gedrückt halten beim Anstecken, `.uf2`-Datei
   auf das erscheinende `RPI-RP2`-Laufwerk ziehen)
2. `pico/main.py` mit [Thonny](https://thonny.org/) oder `mpremote` als
   `main.py` auf den Pico kopieren:
   ```
   mpremote cp pico/main.py :main.py
   ```
3. Nach einem Reset startet die Firmware automatisch (kein Zutun nötig)
4. **Thonny anschließend trennen/schließen**, damit der Desktop-Client
   exklusiv auf den seriellen Port zugreifen kann

### Konfiguration

Baudraten der beiden UARTs lassen sich direkt im Code (`pico/main.py`,
Variablen `UART0_BAUD` / `UART1_BAUD`) oder zur Laufzeit per Befehl ändern
(siehe Protokoll unten).

## Installation – Desktop-Client

```bash
cd client
pip install -r requirements.txt
python rs232_client.py
```

Voraussetzung: `tkinter` (bei den meisten Python-Installationen bereits
enthalten; unter Debian/Ubuntu ggf. `sudo apt install python3-tk`).

### Client-Funktionen

- COM-Port-Auswahl mit Refresh
- Live-Log beider Lanes, farblich getrennt, mit Zeitstempel/Hex/ASCII
- Filter pro Lane und für Info-Meldungen, Autoscroll ein/aus
- Baudrate pro Lane per Dropdown setzen
- Konfigurationsabfrage (`#$i1`)
- Senden von Text oder Hex-Bytes auf jede Lane einzeln
- Log als Datei speichern (Snapshot) oder fortlaufend live mitschreiben

## Serielles Protokoll (USB, Pico ↔ Client)

Alle Nachrichten sind einzelne, mit `\n` abgeschlossene Textzeilen.

### Pico → Client

| Format | Beschreibung |
|--------|--------------|
| `LOG,<lane>,<timestamp_ms>,<hex_bytes>` | Empfangene (oder gesendete) Daten auf einer Lane |
| `INFO,<timestamp_ms>,<text>` | Bestätigungen, Fehlermeldungen |
| `CONFIG,<lane>,<baudrate>` | Antwort auf `#$i1`, eine Zeile je Lane |
| `HELP,<text>` | Antwort auf `#$i1`, Liste der verfügbaren Befehle |

`<lane>` ist `0` (Lane 1 / UART0) oder `1` (Lane 2 / UART1).
`<timestamp_ms>` ist die Millisekundenanzahl seit dem letzten Boot des Pico
(keine Echtzeit – der Pico hat keine batteriegepufferte RTC).

### Client → Pico

| Befehl | Beschreibung | Beispiel |
|--------|--------------|----------|
| `SEND,<lane>,<hex_bytes>` | Daten auf einer Lane senden | `SEND,0,41540D0A` |
| `#$Baud<lane>"<rate>"` | Baudrate einer Lane setzen | `#$Baud0"9600"` |
| `#$Baud<lane>` | Aktuelle Baudrate einer Lane abfragen | `#$Baud0` |
| `#$i1` | Komplette Konfiguration + Befehlsübersicht ausgeben | `#$i1` |

## Bekannte Einschränkungen

- Der Pico hat keine Echtzeituhr – Zeitstempel sind relativ zum Boot-Zeitpunkt
- Bei sehr hohen Baudraten auf beiden Lanes gleichzeitig ist der
  Software-Empfangspuffer (`UART_RXBUF` in `pico/main.py`, Standard 2048 Bytes)
  die begrenzende Größe; bei Bedarf im Code erhöhen
- Es kann jeweils nur eine Anwendung gleichzeitig auf den seriellen Port
  zugreifen (z.B. entweder Thonny **oder** der Desktop-Client, nicht beides)

## Lizenz

Dieses Projekt steht unter der **MIT-Lizenz**.

Kostenlose Nutzung, Veränderung und Weitergabe erlaubt – auch kommerziell –, solange der ursprüngliche Copyright-Hinweis erhalten bleibt.

Siehe [LICENSE](LICENSE) für den vollständigen Lizenztext.
