#!/usr/bin/env python3
"""
prolite_sign.py
================
Controller for Pro-Lite "TruColor II" scrolling LED signs (badged as
TruColorII KCS-M2010, PL-M2014R, PL-M2010, etc.) over RS-232.

These signs all share the same ROM-level ASCII protocol, documented here:
    http://wls.wwco.com/ledsigns/prolite/ProliteProtocol.html
    https://www.instructables.com/Communicating-with-a-Pro-Lite-LED-Display-Cable-C/
        (this one spells out the full <Cxx>/<Sxx>/<Fxx> code tables directly
        from a working build, and is the source used for the tables below)
    https://www.linuxjournal.com/article/2823  (more worked examples)
    ProLite.pm on CPAN (Perl implementation with the same command set)

NOTE ON CONFIDENCE:
  - Wake-up, page write/run/delete, timer, graphic-block, delete-all and
    set-clock commands, plus the full <Cxx> color, <Sxx> size, and <Fxx>
    function code tables, are directly confirmed against the Instructables
    source above (letter-by-letter, from someone who built and used one of
    these cables). Function-code *names* are occasionally a little loose
    (e.g. one build's own comments don't perfectly match the documented
    table for one code), so if a particular effect doesn't look like its
    name suggests, trust what you see over the label - ROM revisions can
    drift slightly. `test-colors` / `test-functions` still exist below to
    let you eyeball everything against your specific unit.

Serial settings (confirmed): 9600 baud, 8 data bits, no parity, 1 stop bit,
no hardware/software flow control. The sign has no buffering/flow control,
so this script paces characters out with a small delay, and waits for the
sign to idle rather than blasting commands back-to-back.
"""

import argparse
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("This tool needs pyserial. Install it with:\n    pip install pyserial")


# ---------------------------------------------------------------------------
# Confirmed/best-known attribute code tables
# ---------------------------------------------------------------------------

# <Cx> color codes - confirmed
COLORS = {
    "A": "dim_red", "B": "red", "C": "bright_red",
    "D": "orange", "E": "bright_orange", "F": "light_yellow",
    "G": "yellow", "H": "bright_yellow", "I": "lime",
    "J": "dim_lime", "K": "bright_lime", "L": "bright_green",
    "M": "green", "N": "dim_green", "O": "yellow_green_red",
    "P": "rainbow_default", "Q": "red_green_3d", "R": "red_yellow_3d",
    "S": "green_red_3d", "T": "green_yellow_3d", "U": "green_on_red",
    "V": "red_on_green", "W": "orange_on_green_3d", "X": "lime_on_red_3d",
    "Y": "green_on_red_3d", "Z": "red_on_green_3d",
}

# <Fx> display-function (transition/effect) codes - confirmed
FUNCTIONS = {
    "A": "random_color_and_effect", "B": "open_from_center", "C": "hide_text",
    "D": "appear", "E": "scrolling_colours", "F": "close_right_to_left",
    "G": "close_left_to_right", "H": "close_toward_center",
    "I": "scroll_up_from_bottom", "J": "scroll_down_from_top",
    "K": "two_layers_slide_together", "L": "falling_dots_form_text",
    "M": "pacman_graphic", "N": "creatures", "O": "beep", "P": "pause",
    "Q": "sleep_blank_screen", "R": "random_dots_form_text",
    "S": "roll_left_to_right", "T": "time_and_date",
    "U": "text_colour_changes_each_time", "V": "thank_you_cursive",
    "W": "welcome_cursive", "X": "speed_1_slow_jittery", "Y": "speed_2",
    "Z": "speed_3",
}

# <Sx> text size/format codes - confirmed
SIZES = {
    "A": "normal", "B": "bold_wide", "C": "italic", "D": "bold_italic_wide",
    "E": "flashing_normal", "F": "flashing_bold_wide", "G": "flashing_italic",
    "H": "flashing_bold_italic_wide",
}

NAME_TO_COLOR = {v: k for k, v in COLORS.items()}
NAME_TO_FUNCTION = {v: k for k, v in FUNCTIONS.items()}
NAME_TO_SIZE = {v: k for k, v in SIZES.items()}

# <Bx> pre-made graphic block references (embed in text, distinct from <Gx>
# which REPROGRAMS a block's pixels) - confirmed
GRAPHICS = {
    "A": "telephone", "B": "glasses", "C": "tap", "D": "rocket", "E": "monster",
    "F": "key", "G": "shirt", "H": "helicopter", "I": "car", "J": "tank",
    "K": "house", "L": "teapot", "M": "knife_and_fork", "N": "duck",
    "O": "motorcycle", "P": "bicycle", "Q": "crown", "R": "sweet_heart",
    "S": "arrow_right", "T": "arrow_left", "U": "arrow_down_left",
    "V": "arrow_up_left", "W": "mug_of_beer", "X": "chair",
    "Y": "high_heeled_shoes", "Z": "wine_glass",
}
NAME_TO_GRAPHIC = {v: k for k, v in GRAPHICS.items()}


class ProLiteSign:
    """One connection to a Pro-Lite / TruColor II sign (or a chain of them)."""

    # sign-baud -> control-byte code, must match BAUD_TABLE in sign_bridge.ino
    BRIDGE_BAUD_CODES = {300: 0, 600: 1, 1200: 2, 2400: 3, 4800: 4, 9600: 5}
    BRIDGE_PC_BAUD = 115200  # fixed PC<->Arduino link speed when bridge=True

    def __init__(self, port, sign_id=1, baud=9600, timeout=2.0, char_delay=0.003,
                 bridge=False):
        self.sign_id = sign_id
        self.char_delay = char_delay
        self.bridge = bridge
        self.sign_baud = baud

        link_baud = self.BRIDGE_PC_BAUD if bridge else baud
        self.ser = serial.Serial(
            port=port,
            baudrate=link_baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
        )
        # Opening a connection to an Arduino Uno normally toggles DTR, which
        # triggers a hardware reset - the board needs ~1.5-2s to finish
        # rebooting and reach loop() before it can process anything we send.
        time.sleep(2.0 if bridge else 0.2)

        if bridge:
            if baud not in self.BRIDGE_BAUD_CODES:
                raise ValueError(
                    f"--bridge only supports these baud rates: "
                    f"{sorted(self.BRIDGE_BAUD_CODES)}")
            # tell the Arduino which sign-side baud to use via the control byte
            self.ser.write(bytes([0x01, self.BRIDGE_BAUD_CODES[baud]]))
            time.sleep(0.05)

    # -- low level -----------------------------------------------------

    def _id_header(self, sign_id=None, all_units=False):
        if all_units:
            return "<ID00>"
        sid = self.sign_id if sign_id is None else sign_id
        return f"<ID{sid:02X}>"

    def _write_raw(self, text):
        """Write text to the wire one byte at a time (the sign has no flow
        control and is easy to outrun)."""
        data = text.encode("ascii", errors="replace")
        for b in data:
            self.ser.write(bytes([b]))
            if self.char_delay:
                time.sleep(self.char_delay)

    def _read_reply(self, timeout=2.0):
        end = time.time() + timeout
        buf = b""
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                if b"\n" in buf:
                    break
            else:
                time.sleep(0.02)
        return buf.decode(errors="replace").strip()

    def send(self, body, sign_id=None, all_units=False, expect_reply=True,
              reply_timeout=2.0):
        """Send `<IDxx>` + body + CRLF. Returns the sign's reply text (which
        should echo `<IDxx>`) or '' if none/expected none."""
        header = self._id_header(sign_id=sign_id, all_units=all_units)
        self._write_raw(header + body + "\r\n")
        if expect_reply and not all_units:
            return self._read_reply(timeout=reply_timeout)
        return ""

    def send_global(self, body):
        """Send a command with NO <IDxx> header (only used for SET CLOCK)."""
        self._write_raw(body + "\r\n")

    # -- wake / housekeeping --------------------------------------------

    def wake(self):
        """Send an empty <IDxx> to wake the sign (needed after ~1 min idle)."""
        return self.send("", expect_reply=True)

    # -- pages ------------------------------------------------------------

    def set_page(self, page, text, color=None, function=None,
                  tail_function=None, size=None, graphic=None):
        """Write `text` into page `page` (letter 'A'-'Z').

        color / function / size may be the single letter code (e.g. 'C') or
        the friendly name (e.g. 'bright_red'). `function` is inserted as a
        LEADING attribute (how text appears), `tail_function` as a TRAILING
        one (how it disappears, e.g. a CLOSE effect) placed after the text.
        `graphic` embeds one of the 26 pre-made <Bx> graphic blocks (letter
        or friendly name, e.g. 'rocket') right before the text.
        """
        page = page.upper()
        assert page in "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "page must be A-Z"

        prefix = ""
        if color:
            prefix += f"<C{self._resolve(color, NAME_TO_COLOR)}>"
        if size:
            prefix += f"<S{self._resolve(size, NAME_TO_SIZE)}>"
        if function:
            prefix += f"<F{self._resolve(function, NAME_TO_FUNCTION)}>"
        if graphic:
            prefix += f"<B{self._resolve(graphic, NAME_TO_GRAPHIC)}>"

        suffix = ""
        if tail_function:
            suffix += f"<F{self._resolve(tail_function, NAME_TO_FUNCTION)}>"

        body = f"<P{page}>{prefix}{text}{suffix}"
        return self.send(body)

    @staticmethod
    def _resolve(value, name_map):
        if len(value) == 1 and value.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            return value.upper()
        key = value.lower()
        if key in name_map:
            return name_map[key]
        raise ValueError(f"Unknown code/name: {value!r}")

    def run_page(self, page):
        """Immediately display page A-Z, or '*' to resume the interrupted timer."""
        page = page.upper()
        return self.send(f"<RP{page}>")

    def delete_page(self, page):
        page = page.upper()
        return self.send(f"<DP{page}>")

    def delete_all_pages(self):
        return self.send("<DP*>")

    # -- timers -------------------------------------------------------

    def set_timer(self, timer_letter, weekday, hour, minute, pages):
        """
        timer_letter: 'A'-'J'
        weekday: '-' (every day) or '0'-'6' (0=Sunday)
        hour: '**' (every hour) or '00'-'23'
        minute: '**' (every minute) or '00'-'59'
        pages: string of up to 32 page letters, e.g. "ABC"
        """
        t = timer_letter.upper()
        body = f"<T{t}>{weekday}{hour}{minute}{pages.upper()}"
        return self.send(body)

    def delete_timer(self, timer_letter):
        return self.send(f"<DT{timer_letter.upper()}>")

    def delete_all_timers(self):
        return self.send("<DT*>")

    # -- graphics -------------------------------------------------------

    def set_graphic(self, block_letter, pattern_rows):
        """
        block_letter: 'A'-'Z'
        pattern_rows: list of 7 strings, each 18 chars long, using
                      R (red) G (green) Y (yellow) B (black/off)
        """
        assert len(pattern_rows) == 7, "need exactly 7 rows"
        for row in pattern_rows:
            assert len(row) == 18, "each row must be 18 characters"
        payload = "".join(pattern_rows)
        assert len(payload) == 126
        return self.send(f"<G{block_letter.upper()}>{payload}")

    def delete_graphic(self, block_letter):
        return self.send(f"<DG{block_letter.upper()}>")

    def delete_all_graphics(self):
        return self.send("<DG*>")

    # -- global ---------------------------------------------------------

    def delete_all(self):
        """Deletes ALL pages, timers, and restores default graphics."""
        return self.send("<D*>")

    def set_clock(self, when=None):
        """Global command (no <IDxx> header) - sets date/time on the sign(s)."""
        when = when or time.localtime()
        yy = when.tm_year % 100
        weekday = (when.tm_wday + 1) % 7  # python Mon=0 -> sign Sun=0
        body = (f"<T{yy:02d}{when.tm_mon:02d}{when.tm_mday:02d}"
                f"{weekday}{when.tm_hour:02d}{when.tm_min:02d}{when.tm_sec:02d}>")
        self.send_global(body)

    def reset(self):
        """Undocumented-but-reported <RST> command that resets the sign."""
        return self.send("<RST>")

    # -- trivia -----------------------------------------------------------
    # Trivia splits memory into two 16K banks and cycles: question, your
    # message, answer, your message, next question, ... The doc recommends
    # using unit ID 00 for trivia commands.

    def set_trivia(self, qa_pairs, sign_id=0):
        """qa_pairs: list of (question, answer) string tuples."""
        self.send("<Q+>", sign_id=sign_id, expect_reply=False)
        for question, answer in qa_pairs:
            self._write_raw(self._id_header(sign_id=sign_id) + question + "\r\n")
            self._write_raw(self._id_header(sign_id=sign_id) + answer + "\r\n")
        return self.send("<Q->", sign_id=sign_id)

    def clear_trivia(self, sign_id=0):
        """Sending <Q+> immediately followed by <Q-> with nothing between
        erases trivia and frees its memory, per the protocol notes."""
        self.send("<Q+>", sign_id=sign_id, expect_reply=False)
        return self.send("<Q->", sign_id=sign_id)

    def close(self):
        self.ser.close()



# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def list_ports():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    for p in ports:
        print(f"{p.device}\t{p.description}")


def main():
    ap = argparse.ArgumentParser(description="Control a Pro-Lite/TruColor II LED sign")
    ap.add_argument("--port", help="Serial port, e.g. COM3 or /dev/ttyUSB0")
    ap.add_argument("--id", type=int, default=1, help="Sign unit ID (default 1)")
    ap.add_argument("--baud", type=int, default=9600, help="Sign's baud rate (default 9600)")
    ap.add_argument("--bridge", action="store_true",
                     help="Connect through the Arduino sign_bridge.ino sketch "
                          "(fixed 115200 PC link, --baud sets the sign-side rate)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-ports", help="List available serial ports")

    p_scan = sub.add_parser("scan-baud", help="Try every standard baud rate and report which one gets a reply")
    p_scan.add_argument("--per-baud-timeout", type=float, default=2.5,
                         help="Seconds to wait for a reply at each baud rate")

    p_wake = sub.add_parser("wake", help="Wake the sign up")

    p_text = sub.add_parser("text", help="Write text to a page")
    p_text.add_argument("message")
    p_text.add_argument("--page", default="A")
    p_text.add_argument("--color", default=None, help="e.g. C or bright_red")
    p_text.add_argument("--function", default=None, help="e.g. D or appear_instant")
    p_text.add_argument("--tail", default=None, help="trailing function code/name")
    p_text.add_argument("--run", action="store_true", help="also run the page immediately")

    p_run = sub.add_parser("run", help="Immediately display a page")
    p_run.add_argument("page")

    p_del = sub.add_parser("delete-page", help="Delete a page")
    p_del.add_argument("page")

    sub.add_parser("delete-all", help="Delete ALL pages/timers, reset graphics")

    p_clock = sub.add_parser("set-clock", help="Sync sign clock to this computer's time")

    p_raw = sub.add_parser("raw", help="Send a raw command body (after <IDxx>)")
    p_raw.add_argument("body")

    sub.add_parser("test-colors", help="Cycle through all 26 color codes on page A so you can verify them")
    sub.add_parser("test-functions", help="Cycle through all 26 function codes on page A so you can verify them")

    args = ap.parse_args()

    if args.cmd == "list-ports":
        list_ports()
        return

    if not args.port:
        sys.exit("Need --port (run `list-ports` to see available ports)")

    if args.cmd == "scan-baud":
        for baud in (300, 600, 1200, 2400, 4800, 9600):
            print(f"Trying {baud} baud ...")
            try:
                s = ProLiteSign(args.port, sign_id=args.id, baud=baud, bridge=args.bridge)
            except Exception as e:
                print(f"  could not open port at {baud}: {e}")
                continue
            try:
                reply = s.wake()
                s.wake()  # try twice - the first send after a baud change is sometimes eaten as garbage
                reply = s.wake()
            finally:
                s.close()
            if reply:
                print(f"  -> got a reply: {reply!r}")
                print(f"\nThe sign is set to {baud} baud. Use --baud {baud} on future commands.")
                return
            else:
                print("  -> no reply")
        print("\nNo reply at any standard baud rate. This points at wiring, power, "
              "or the sign's ID rather than baud rate.")
        return

    sign = ProLiteSign(args.port, sign_id=args.id, baud=args.baud, bridge=args.bridge)
    try:
        if args.cmd != "set-clock":
            print("wake ->", repr(sign.wake()))

        if args.cmd == "wake":
            pass
        elif args.cmd == "text":
            reply = sign.set_page(args.page, args.message, color=args.color,
                                   function=args.function, tail_function=args.tail)
            print("set_page ->", repr(reply))
            if args.run:
                print("run_page ->", repr(sign.run_page(args.page)))
        elif args.cmd == "run":
            print("run_page ->", repr(sign.run_page(args.page)))
        elif args.cmd == "delete-page":
            print("delete_page ->", repr(sign.delete_page(args.page)))
        elif args.cmd == "delete-all":
            confirm = input("This deletes ALL pages/timers/graphics. Type YES to continue: ")
            if confirm == "YES":
                print("delete_all ->", repr(sign.delete_all()))
            else:
                print("Cancelled.")
        elif args.cmd == "set-clock":
            sign.set_clock()
            print("Clock set (no reply expected for this global command).")
        elif args.cmd == "raw":
            print("send ->", repr(sign.send(args.body)))
        elif args.cmd == "test-colors":
            for letter, name in COLORS.items():
                print(f"Showing color {letter} ({name}) ...")
                sign.set_page("A", f"COLOR {letter} {name}", color=letter,
                               function="appear_instant")
                sign.run_page("A")
                time.sleep(2.5)
        elif args.cmd == "test-functions":
            for letter, name in FUNCTIONS.items():
                print(f"Showing function {letter} ({name}) ...")
                sign.set_page("A", f"FUNC {letter} {name}", function=letter)
                sign.run_page("A")
                time.sleep(3.5)
    finally:
        sign.close()


if __name__ == "__main__":
    main()
