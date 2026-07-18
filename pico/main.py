"""
RS232 Dual-Lane UART<->USB Bridge für Raspberry Pi Pico (kein W nötig)
------------------------------------------------------------------------
Lane 1 = UART0 (GP0=TX, GP1=RX) -> externe Leitung 1 (via MAX3232)
Lane 2 = UART1 (GP4=TX, GP5=RX) -> externe Leitung 2 (via MAX3232)

Der Pico macht hier NUR die UART-Ein-/Ausgabe. Alle Log-Zeilen werden
per print() auf die USB-Seriell-Schnittstelle ausgegeben und können
am PC direkt über den COM-Port mit einem beliebigen Terminal-Programm
gelesen werden (kein zusätzlicher Host-Server nötig). Über denselben
COM-Port lassen sich auch Befehle an den Pico zurücksenden (Senden
von Daten, Baudrate ändern – siehe Protokoll unten).

Protokoll (Zeilen, \\n-terminiert):
  Pico -> Host:  LOG,<lane>,<timestamp_ms>,<hex_bytes>
  Pico -> Host:  INFO,<timestamp_ms>,<freitext>   (Bestaetigungen/Fehler)
  Pico -> Host:  CONFIG,<lane>,<baudrate>          (Antwort auf #$i1, eine Zeile je Lane)
  Pico -> Host:  HELP,<beschreibung>                (Antwort auf #$i1, Liste verfuegbarer Befehle)
  Host -> Pico:  SEND,<lane>,<hex_bytes>            (Daten auf UART senden)
  Host -> Pico:  #$Baud<lane>"<rate>"               (Baudrate umstellen)
                 Beispiele: #$Baud0"9600"   #$Baud1"115200"
  Host -> Pico:  #$Baud<lane>                       (ohne Anfuehrungszeichen:
                 aktuelle Baudrate der Lane per INFO zurueckgemeldet)
                 Beispiel: #$Baud0
  Host -> Pico:  #$i1                                (gibt Konfiguration
                 aller Lanes als CONFIG-Zeilen aus)

Die komplette Ausgabe (LOG-/INFO-Zeilen) laeuft direkt ueber die
USB-Seriell-Schnittstelle -> einfach mit einem beliebigen
COM-Port-Terminal (PuTTY, CoolTerm, screen, minicom, ...) mitlesen.

WICHTIG: Sobald dieses Skript als main.py auf dem Pico laeuft, darf
gleichzeitig nur EIN Programm den seriellen Port geoeffnet haben.
Zum dauerhaften Betrieb also Thonny trennen und stattdessen dein
Terminal-Tool der Wahl verwenden.
"""

from machine import UART, Pin
import sys
import select
import utime
import ubinascii

# ----------------------- KONFIGURATION -----------------------

UART0_BAUD = 115200   # Lane 1
UART1_BAUD = 115200   # Lane 2

# Software-Empfangspuffer je UART (Bytes). Der Hardware-FIFO fasst nur 32
# Bytes; bei hohen Baudraten auf beiden Lanes gleichzeitig braucht es einen
# groesseren Zwischenpuffer, damit zwischen zwei Poll-Durchlaeufen nichts
# verloren geht.
UART_RXBUF = 2048

# ----------------------------------------------------------------

uart0 = UART(0, baudrate=UART0_BAUD, tx=Pin(0), rx=Pin(1), rxbuf=UART_RXBUF)
uart1 = UART(1, baudrate=UART1_BAUD, tx=Pin(4), rx=Pin(5), rxbuf=UART_RXBUF)
UARTS = {0: uart0, 1: uart1}
CURRENT_BAUD = {0: UART0_BAUD, 1: UART1_BAUD}

# Non-blocking Zugriff auf USB-Seriell (stdin) via poll
poller = select.poll()
poller.register(sys.stdin, select.POLLIN)


def send_log(lane: int, data: bytes):
    hex_str = ubinascii.hexlify(data).decode()
    print("LOG,{},{},{}".format(lane, utime.ticks_ms(), hex_str))


def send_info(text: str):
    print("INFO,{},{}".format(utime.ticks_ms(), text))


AVAILABLE_COMMANDS = [
    'SEND,<lane>,<hex_bytes>          - Daten auf UART senden (lane 0 oder 1)',
    '#$Baud<lane>"<rate>"             - Baudrate setzen, z.B. #$Baud0"9600"',
    '#$Baud<lane>                     - aktuelle Baudrate der Lane abfragen',
    '#$i1                             - diese Konfigurations-/Hilfeausgabe',
]


def send_config_dump():
    for lane in sorted(UARTS.keys()):
        print("CONFIG,{},{}".format(lane, CURRENT_BAUD[lane]))
    for cmd in AVAILABLE_COMMANDS:
        print("HELP," + cmd)


def try_handle_baud_command(line: str) -> bool:
    # Format: #$Baud<lane>"<rate>"   z.B.  #$Baud0"9600"   oder  #$Baud1"115200"
    # Ohne "<rate>"-Teil (z.B. #$Baud0) wird stattdessen die aktuelle
    # Baudrate der Lane zurueckgemeldet.
    if not line.startswith("#$Baud"):
        return False
    rest = line[len("#$Baud"):]
    if len(rest) < 1 or rest[0] not in ("0", "1"):
        send_info("Ungueltiger Baud-Befehl (Lane fehlt/falsch): " + line)
        return True
    lane = int(rest[0])
    rate_part = rest[1:]

    if rate_part == "":
        send_info("Lane{} aktuelle Baudrate: {}".format(lane, CURRENT_BAUD[lane]))
        return True

    if not (rate_part.startswith('"') and rate_part.endswith('"') and len(rate_part) >= 3):
        send_info("Ungueltiger Baud-Befehl (Anfuehrungszeichen fehlen): " + line)
        return True
    rate_str = rate_part[1:-1]
    try:
        new_baud = int(rate_str)
    except Exception:
        send_info("Ungueltige Baudrate: " + rate_str)
        return True
    try:
        UARTS[lane].init(baudrate=new_baud, rxbuf=UART_RXBUF)
        CURRENT_BAUD[lane] = new_baud
        send_info("Lane{} Baudrate auf {} gesetzt".format(lane, new_baud))
    except Exception as e:
        send_info("Fehler beim Setzen der Baudrate: " + str(e))
    return True


def handle_incoming_line(line: str):
    line = line.strip()
    if not line:
        return

    if line == "#$i1":
        send_config_dump()
        return

    if try_handle_baud_command(line):
        return

    parts = line.split(",", 2)
    if len(parts) != 3 or parts[0] != "SEND":
        send_info("Unbekannter Befehl: " + line)
        return
    try:
        lane = int(parts[1])
    except Exception:
        send_info("SEND: ungueltige Lane '{}' in: {}".format(parts[1], line))
        return
    if lane not in UARTS:
        send_info("SEND: Lane {} existiert nicht (nur 0 oder 1)".format(lane))
        return
    try:
        payload = ubinascii.unhexlify(parts[2])
    except Exception as e:
        send_info("SEND: ungueltige Hex-Daten '{}' ({})".format(parts[2], str(e)))
        return
    if not payload:
        send_info("SEND: leere Daten, nichts gesendet")
        return
    UARTS[lane].write(payload)
    send_log(lane, payload)  # eigenes Echo als Log-Zeile


def main():
    rx_buf = ""
    while True:
        # 1) UART-Daten einsammeln und loggen
        for lane, u in UARTS.items():
            if u.any():
                data = u.read()
                if data:
                    send_log(lane, data)

        # 2) Eingehende Kommandos vom Host (nicht-blockierend) lesen
        events = poller.poll(0)
        if events:
            ch = sys.stdin.read(1)
            if ch:
                if ch == "\n":
                    handle_incoming_line(rx_buf)
                    rx_buf = ""
                elif ch != "\r":
                    rx_buf += ch

        utime.sleep_ms(1)


main()
