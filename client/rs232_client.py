"""
RS232 Dual-Lane Logger - Desktop GUI Client
---------------------------------------------
Verbindet sich über die serielle USB-Schnittstelle mit dem Pico
(main.py) und stellt Live-Log, Konfiguration und Senden komfortabel
über eine Tkinter-GUI bereit.

Abhängigkeiten:
    pip install pyserial

Start:
    python rs232_client.py
"""

import sys
import threading
import queue
import time
import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("pyserial fehlt. Installieren mit: pip install pyserial")
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


LANE_NAMES = {0: "Lane1 (UART0)", 1: "Lane2 (UART1)"}
COMMON_BAUDS = ["1200", "2400", "4800", "9600", "19200", "38400",
                "57600", "115200", "230400"]


def to_printable(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b <= 126 else "." for b in data)


def now_str() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


class SerialWorker(threading.Thread):
    """Liest Zeilen vom Pico in einem eigenen Thread und legt sie in eine Queue."""

    def __init__(self, port: str, baud: int, out_queue: queue.Queue, status_queue: queue.Queue):
        super().__init__(daemon=True)
        self.port_name = port
        self.baud = baud
        self.out_queue = out_queue
        self.status_queue = status_queue
        self.ser = None
        self._stop_flag = threading.Event()

    def run(self):
        try:
            self.ser = serial.Serial(self.port_name, self.baud, timeout=0.2)
        except Exception as e:
            self.status_queue.put(("error", "Verbindung fehlgeschlagen: {}".format(e)))
            return
        self.status_queue.put(("connected", self.port_name))
        buf = b""
        while not self._stop_flag.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as e:
                self.status_queue.put(("error", "Lesefehler: {}".format(e)))
                break
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode(errors="replace").strip("\r")
                    if text:
                        self.out_queue.put(text)
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.status_queue.put(("disconnected", self.port_name))

    def write_line(self, text: str):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write((text + "\n").encode())
            except Exception as e:
                self.status_queue.put(("error", "Schreibfehler: {}".format(e)))

    def stop(self):
        self._stop_flag.set()


class RS232ClientApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RS232 Dual-Lane Logger")
        self.root.geometry("1000x700")

        self.worker = None
        self.rx_queue = queue.Queue()
        self.status_queue = queue.Queue()

        self.autoscroll = tk.BooleanVar(value=True)
        self.continuous_log = tk.BooleanVar(value=False)
        self.log_file_handle = None

        self.filter_lane0 = tk.BooleanVar(value=True)
        self.filter_lane1 = tk.BooleanVar(value=True)
        self.filter_info = tk.BooleanVar(value=True)

        self.all_lines = []  # (raw_text, tag) für Filter-Neuaufbau

        self._build_ui()
        self._poll_queues()

    # ---------------------------------------------------------- UI Aufbau

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        # --- Verbindung ---
        conn_frame = ttk.LabelFrame(top, text="Verbindung", padding=6)
        conn_frame.pack(side="left", fill="y", padx=(0, 8))

        ttk.Label(conn_frame, text="Port:").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(conn_frame, width=14, state="readonly")
        self.port_combo.grid(row=0, column=1, padx=4)
        ttk.Button(conn_frame, text="🔄", width=3, command=self.refresh_ports).grid(row=0, column=2)

        ttk.Label(conn_frame, text="USB-Baud:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.usb_baud_entry = ttk.Entry(conn_frame, width=10)
        self.usb_baud_entry.insert(0, "115200")
        self.usb_baud_entry.grid(row=1, column=1, sticky="w", pady=(4, 0))

        self.connect_btn = ttk.Button(conn_frame, text="Verbinden", command=self.toggle_connection)
        self.connect_btn.grid(row=2, column=0, columnspan=3, pady=(6, 0), sticky="we")

        self.status_label = ttk.Label(conn_frame, text="Getrennt", foreground="red")
        self.status_label.grid(row=3, column=0, columnspan=3, pady=(4, 0))

        # --- Lane-Konfiguration ---
        cfg_frame = ttk.LabelFrame(top, text="Lane-Baudraten", padding=6)
        cfg_frame.pack(side="left", fill="y", padx=(0, 8))

        self.lane_baud_widgets = {}
        for lane in (0, 1):
            ttk.Label(cfg_frame, text=LANE_NAMES[lane] + ":").grid(row=lane, column=0, sticky="w")
            combo = ttk.Combobox(cfg_frame, values=COMMON_BAUDS, width=10)
            combo.set("115200")
            combo.grid(row=lane, column=1, padx=4)
            btn = ttk.Button(cfg_frame, text="Setzen", width=8,
                              command=lambda l=lane: self.set_baud(l))
            btn.grid(row=lane, column=2, padx=2)
            self.lane_baud_widgets[lane] = combo

        ttk.Button(cfg_frame, text="Konfiguration abfragen (#$i1)",
                   command=self.query_config).grid(row=2, column=0, columnspan=3, pady=(6, 0), sticky="we")

        # --- Log-Optionen ---
        log_opt_frame = ttk.LabelFrame(top, text="Log-Optionen", padding=6)
        log_opt_frame.pack(side="left", fill="y")

        ttk.Checkbutton(log_opt_frame, text="Autoscroll", variable=self.autoscroll).grid(
            row=0, column=0, sticky="w")
        ttk.Checkbutton(log_opt_frame, text="Lane1 anzeigen", variable=self.filter_lane0,
                         command=self.rebuild_log_view).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(log_opt_frame, text="Lane2 anzeigen", variable=self.filter_lane1,
                         command=self.rebuild_log_view).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(log_opt_frame, text="Info anzeigen", variable=self.filter_info,
                         command=self.rebuild_log_view).grid(row=3, column=0, sticky="w")

        ttk.Button(log_opt_frame, text="Log leeren", command=self.clear_log).grid(
            row=4, column=0, sticky="we", pady=(4, 0))
        ttk.Button(log_opt_frame, text="Log speichern unter...", command=self.save_log_as).grid(
            row=5, column=0, sticky="we", pady=(2, 0))
        ttk.Checkbutton(log_opt_frame, text="Fortlaufend mitschreiben",
                         variable=self.continuous_log,
                         command=self.toggle_continuous_log).grid(row=6, column=0, sticky="w", pady=(2, 0))

        # --- Log-Anzeige ---
        log_frame = ttk.Frame(self.root, padding=(8, 0))
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, wrap="none", bg="#111111", fg="#dddddd",
                                 insertbackground="#dddddd", font=("Consolas", 10))
        self.log_text.tag_config("lane0", foreground="#7fffa0")
        self.log_text.tag_config("lane1", foreground="#ffd27f")
        self.log_text.tag_config("info", foreground="#7fdcff")
        self.log_text.tag_config("raw", foreground="#999999")

        yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        xscroll = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="we")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text.configure(state="disabled")

        # --- Senden ---
        send_frame = ttk.LabelFrame(self.root, text="Senden", padding=8)
        send_frame.pack(fill="x", padx=8, pady=8)

        self.send_widgets = {}
        for lane in (0, 1):
            row = ttk.Frame(send_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=LANE_NAMES[lane] + ":", width=16).pack(side="left")
            entry = ttk.Entry(row)
            entry.pack(side="left", fill="x", expand=True, padx=4)
            entry.bind("<Return>", lambda e, l=lane: self.send_data(l))
            hex_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(row, text="Hex", variable=hex_var).pack(side="left", padx=4)
            ttk.Button(row, text="Senden", command=lambda l=lane: self.send_data(l)).pack(side="left")
            self.send_widgets[lane] = {"entry": entry, "hex": hex_var}

        # --- Statuszeile ---
        self.footer = ttk.Label(self.root, text="Bereit.", anchor="w", relief="sunken")
        self.footer.pack(fill="x", side="bottom")

        self.refresh_ports()

    # ---------------------------------------------------------- Verbindung

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.port_combo.get():
            self.port_combo.set(ports[0])

    def toggle_connection(self):
        if self.worker is not None:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_combo.get()
        if not port:
            messagebox.showwarning("Kein Port", "Bitte einen COM-Port auswählen.")
            return
        try:
            baud = int(self.usb_baud_entry.get())
        except ValueError:
            baud = 115200
        self.worker = SerialWorker(port, baud, self.rx_queue, self.status_queue)
        self.worker.start()
        self.connect_btn.config(text="Trennen")

    def disconnect(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.connect_btn.config(text="Verbinden")
        self.status_label.config(text="Getrennt", foreground="red")

    # ---------------------------------------------------------- Kommandos

    def set_baud(self, lane: int):
        if not self.worker:
            messagebox.showinfo("Nicht verbunden", "Bitte zuerst verbinden.")
            return
        rate = self.lane_baud_widgets[lane].get().strip()
        if not rate.isdigit():
            messagebox.showwarning("Ungültig", "Baudrate muss eine Zahl sein.")
            return
        cmd = '#$Baud{}"{}"'.format(lane, rate)
        self.worker.write_line(cmd)
        self.footer.config(text="Gesendet: " + cmd)

    def query_config(self):
        if not self.worker:
            messagebox.showinfo("Nicht verbunden", "Bitte zuerst verbinden.")
            return
        self.worker.write_line("#$i1")
        self.footer.config(text="Konfiguration abgefragt (#$i1)")

    def send_data(self, lane: int):
        if not self.worker:
            messagebox.showinfo("Nicht verbunden", "Bitte zuerst verbinden.")
            return
        widgets = self.send_widgets[lane]
        text = widgets["entry"].get()
        if not text:
            return
        if widgets["hex"].get():
            clean = text.replace(" ", "")
            try:
                payload_hex = bytes.fromhex(clean).hex()
            except ValueError:
                messagebox.showwarning("Ungültiges Hex", "Bitte gültige Hex-Bytes eingeben (z.B. 41 0D 0A).")
                return
        else:
            payload_hex = text.encode().hex()
        cmd = "SEND,{},{}".format(lane, payload_hex)
        self.worker.write_line(cmd)
        widgets["entry"].delete(0, "end")
        self.footer.config(text="Gesendet: " + cmd)

    # ---------------------------------------------------------- Log-Verarbeitung

    def parse_line(self, raw: str):
        """Gibt (anzeige_text, tag) zurück."""
        parts = raw.split(",", 3)
        if parts[0] == "LOG" and len(parts) == 4:
            try:
                lane = int(parts[1])
                ts = parts[2]
                data = bytes.fromhex(parts[3])
                ascii_repr = to_printable(data)
                text = "[{}] ms={:>10} {:<20} HEX: {:<30} ASCII: {}".format(
                    now_str(), ts, LANE_NAMES.get(lane, "Lane?"), data.hex(" "), ascii_repr)
                tag = "lane0" if lane == 0 else "lane1"
                return text, tag
            except Exception:
                return raw, "raw"
        elif parts[0] == "INFO" and len(parts) >= 3:
            ts = parts[1]
            msg = raw.split(",", 2)[2] if len(raw.split(",", 2)) == 3 else ""
            text = "[{}] ms={:>10} INFO: {}".format(now_str(), ts, msg)
            return text, "info"
        elif parts[0] in ("CONFIG", "HELP"):
            text = "[{}] {}".format(now_str(), raw)
            return text, "info"
        else:
            return "[{}] {}".format(now_str(), raw), "raw"

    def append_log(self, raw: str):
        text, tag = self.parse_line(raw)
        self.all_lines.append((text, tag, raw))
        if self._passes_filter(tag):
            self._write_to_widget(text, tag)
        if self.continuous_log.get() and self.log_file_handle:
            try:
                self.log_file_handle.write(raw + "\n")
                self.log_file_handle.flush()
            except Exception:
                pass

    def _passes_filter(self, tag: str) -> bool:
        if tag == "lane0":
            return self.filter_lane0.get()
        if tag == "lane1":
            return self.filter_lane1.get()
        if tag == "info":
            return self.filter_info.get()
        return True  # raw / unbekannte Zeilen immer anzeigen

    def _write_to_widget(self, text: str, tag: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.configure(state="disabled")
        if self.autoscroll.get():
            self.log_text.see("end")

    def rebuild_log_view(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        for text, tag, _raw in self.all_lines:
            if self._passes_filter(tag):
                self._write_to_widget(text, tag)

    def clear_log(self):
        self.all_lines.clear()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def save_log_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Textdatei", "*.txt"), ("Alle Dateien", "*.*")],
            initialfile="rs232_log_{}.txt".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for text, _tag, _raw in self.all_lines:
                    f.write(text + "\n")
            self.footer.config(text="Log gespeichert: " + path)
        except Exception as e:
            messagebox.showerror("Fehler", "Konnte Log nicht speichern: {}".format(e))

    def toggle_continuous_log(self):
        if self.continuous_log.get():
            path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("Textdatei", "*.txt"), ("Alle Dateien", "*.*")],
                initialfile="rs232_live_{}.txt".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
            if not path:
                self.continuous_log.set(False)
                return
            try:
                self.log_file_handle = open(path, "a", encoding="utf-8")
                self.footer.config(text="Schreibe fortlaufend in: " + path)
            except Exception as e:
                messagebox.showerror("Fehler", "Konnte Datei nicht öffnen: {}".format(e))
                self.continuous_log.set(False)
        else:
            if self.log_file_handle:
                try:
                    self.log_file_handle.close()
                except Exception:
                    pass
                self.log_file_handle = None
            self.footer.config(text="Fortlaufendes Mitschreiben gestoppt.")

    # ---------------------------------------------------------- Queue-Polling

    def _poll_queues(self):
        try:
            while True:
                raw = self.rx_queue.get_nowait()
                self.append_log(raw)
        except queue.Empty:
            pass

        try:
            while True:
                kind, info = self.status_queue.get_nowait()
                if kind == "connected":
                    self.status_label.config(text="Verbunden: " + info, foreground="green")
                elif kind == "disconnected":
                    self.status_label.config(text="Getrennt", foreground="red")
                    self.connect_btn.config(text="Verbinden")
                    self.worker = None
                elif kind == "error":
                    self.status_label.config(text="Fehler", foreground="red")
                    self.footer.config(text=info)
                    self.worker = None
                    self.connect_btn.config(text="Verbinden")
        except queue.Empty:
            pass

        self.root.after(50, self._poll_queues)

    def on_close(self):
        if self.worker:
            self.worker.stop()
        if self.log_file_handle:
            try:
                self.log_file_handle.close()
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = RS232ClientApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
