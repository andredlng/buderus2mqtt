#
# buderus2mqtt - Buderus Logamatic 4000 serial to MQTT bridge
#
# Reads binary data from the Buderus Logamatic 4000 series heating controller
# via serial interface and publishes decoded values to MQTT.
#
# Based on l4000-daemon.pl v2.0 by Peter G. Holzleitner (2015-2022)
# Python port (c) 2026
#

import logging
import subprocess
import time

import serial

import iot_daemonize


logger = logging.getLogger('buderus2mqtt')

config = None
run_counters: dict[str, int] = {}


# --- Protocol helpers ---

def checksum(block: list[int]) -> int:
    """XOR-based checksum with bit rotation and polynomial 0x19."""
    cs = 0
    for n in range(8):
        b = block[n]
        for _ in range(n):
            if b < 0x80:
                b = (b << 1) & 0xFF
            else:
                b = (b & 0x7F) << 1 & 0xFF
                b ^= 0x19
        cs ^= b
    return cs


def signed_byte(b: int) -> int:
    return b - 256 if b > 127 else b


def reclen(name: str, expected: int, actual: int) -> bool:
    if expected > 0 and actual != expected:
        logger.warning('Invalid record length for %s - expected %d, got %d', name, expected, actual)
        return False
    if expected < 0 and actual < -expected:
        logger.warning('Invalid record length for %s - expected at least %d, got %d', name, -expected, actual)
        return False
    return True


# --- Record decoders ---

RECORD_HANDLERS: dict[int, callable] = {}


def init_record_handlers():
    RECORD_HANDLERS.update({
        0x80: lambda r: decode_zone(1, r),
        0x81: lambda r: decode_zone(2, r),
        0x82: lambda r: decode_zone(3, r),
        0x83: lambda r: decode_zone(4, r),
        0x8a: lambda r: decode_zone(5, r),
        0x8b: lambda r: decode_zone(6, r),
        0x8c: lambda r: decode_zone(7, r),
        0x8d: lambda r: decode_zone(8, r),
        0x8e: lambda r: decode_zone(9, r),
        0x84: decode_water,
        0x87: decode_errlog,
        0x88: decode_boiler,
        0x89: decode_config,
        0x9B: decode_energy,
        0x9E: decode_solar,
    })


def decode(recnum: int, record: bytes):
    logger.debug('Record 0x%02x (%d bytes) = %s', recnum, len(record), record.hex())
    handler = RECORD_HANDLERS.get(recnum)
    if handler:
        handler(record)


def decode_zone(zone: int, record: bytes):
    key = f'zone{zone}'
    run = run_counters.get(key, 0)
    run_counters[key] = run + 1

    rb = list(record)
    if not reclen(f'zone {zone}', 18, len(record)):
        return

    vs = rb[2]
    vi = rb[3]
    rs = rb[4] / 2.0
    ri = rb[5] / 2.0
    o1 = rb[6]
    o0 = rb[7]
    pu = rb[8]
    sg = signed_byte(rb[9])
    k1 = rb[12]
    k2 = rb[13]
    k3 = rb[14]

    # Status flags
    st = []
    if rb[0] & 0x01: st.append('Aus-Opt')
    if rb[0] & 0x02: st.append('Ein-Opt')
    if rb[0] & 0x04: st.append('Auto')
    if rb[0] & 0x08: st.append('WW-Vorrang')
    if rb[0] & 0x10: st.append('Estrich-Tr')
    if rb[0] & 0x20: st.append('Ferien')
    if rb[0] & 0x40: st.append('Frostschutz')
    if rb[0] & 0x80: st.append('Manuell')
    if rb[1] & 0x01: st.append('Sommer')
    if rb[1] & 0x02: st.append('Tag')
    if rb[10] & 0x01: st.append('WF2-EIN')
    if rb[10] & 0x02: st.append('WF3-EIN')

    # Error flags
    err = []
    if rb[1] & 0x04: err.append('Fernbedienungs-Kommunikation gestoert')
    if rb[1] & 0x08: err.append('Fernbedienungs-Fehler')
    if rb[1] & 0x10: err.append('Fehler Vorlauffuehler')
    if rb[1] & 0x20: err.append('Maximale Vorlauftemperatur')
    if rb[1] & 0x40: err.append('Externe Fehlermeldung')
    if rb[10] & 0x20: err.append('Betriebsartschalter: AUS')
    if rb[10] & 0x40: err.append('Betriebsartschalter: MANUELL')

    ri_str = '----' if ri == 55.0 else f'{ri:.1f}'  # rb[5] == 110 -> 55.0 = invalid sensor
    vi_str = '----' if vi == 110 else str(vi)  # rb[3] == 110 = no flow temp sensor
    logger.info('Heizkreis %d: Raum Soll/Ist = %.1f/%s C, Vorlauf Soll/Ist = %d/%s  %s  %s',
                zone, rs, ri_str, vs, vi_str, ' '.join(st), '; '.join(err))
    logger.info('             Pumpe %d%%  Stellglied %d%%  Ein-Opt. %d min  Aus-Opt. %d min',
                pu, sg, o1, o0)
    logger.info('             Kennlinie: AT -10/0/+10 AT -> VL %d/%d/%d', k3, k2, k1)

    if run % 2 == (zone % 2):
        data = {
            f'hk{zone}_s': rs,
            f'hk{zone}_sg': sg,
            f'hk{zone}_vs': vs,
            f'hk{zone}_err': '; '.join(err),
        }
        if vi != 110:  # rb[3] == 110 = no flow temp sensor
            data[f'hk{zone}_v'] = vi
        if ri != 55.0:  # rb[5] == 110 -> 55.0 = invalid sensor
            data[f'hk{zone}'] = ri
            data[f'hk{zone}_pu'] = pu
        send_data(data)


def decode_water(record: bytes):
    run = run_counters.get('water', 0)
    run_counters['water'] = run + 1

    rb = list(record)
    if not reclen('water', 12, len(record)):
        return

    ws = rb[2]
    wi = rb[3]

    # Status flags
    st = []
    if rb[0] & 0x01: st.append('Auto')
    if rb[0] & 0x02: st.append('Desinfektion')
    if rb[0] & 0x04: st.append('Nachladung')
    if rb[0] & 0x08: st.append('Ferien')
    if rb[1] & 0x01: st.append('Laden')
    if rb[1] & 0x02: st.append('Manuell')
    if rb[1] & 0x04: st.append('Nachladen')
    if rb[1] & 0x08: st.append('A-Opt')
    if rb[1] & 0x10: st.append('E-Opt')
    if rb[1] & 0x20: st.append('Tag')
    if rb[1] & 0x40: st.append('Warm')
    if rb[1] & 0x80: st.append('Vorrang')
    if rb[5] & 0x01: st.append('Ladepumpe')
    if rb[5] & 0x02: st.append('Zirk.-Pumpe')
    if rb[5] & 0x04: st.append('Abs.-Solar')
    if rb[6] & 0x01: st.append('WF2-EIN')
    if rb[6] & 0x02: st.append('WF3-EIN')

    # Error flags
    err = []
    if rb[0] & 0x10: err.append('Fehler bei Desinfektion')
    if rb[0] & 0x20: err.append('Fehler in Temperatursensor')
    if rb[0] & 0x40: err.append('Fehler - Warmwasser bleibt kalt')
    if rb[0] & 0x80: err.append('Fehler in Inertanode')
    if rb[6] & 0x20: err.append('Betriebsartschalter: AUS')
    if rb[6] & 0x40: err.append('Betriebsartschalter: MANUELL')
    if rb[7] & 0x01: err.append('Externe Fehlermeldung')

    la = 1 if rb[1] & 0x01 else 0
    p0 = 1 if rb[5] & 0x01 else 0
    p1 = 1 if rb[5] & 0x02 else 0

    logger.info('Warmwasser:  Soll/Ist = %s/%s C  %s %s', ws, wi, ' '.join(st), '; '.join(err))

    if run % 2 == 0:
        send_data({'ww': wi, 'ww_s': ws, 'ww_l': la, 'ww_laden': p0, 'ww_zirk': p1,
                   'ww_err': '; '.join(err)})


def decode_errlog(record: bytes):
    run_counters['errlog'] = run_counters.get('errlog', 0) + 1

    if not reclen('error log', 42, len(record)):
        return

    # TBI - error log decoding not yet implemented


def decode_boiler(record: bytes):
    run = run_counters.get('boiler', 0)
    run_counters['boiler'] = run + 1

    rb = list(record)
    if not reclen('boiler', 42, len(record)):
        return

    ks = rb[0]
    ki = rb[1]
    k1 = rb[2]
    k0 = rb[3]
    br = rb[8]

    # Error flags
    err = []
    if rb[6] & 0x01: err.append('BRENNERSTOERUNG')
    if rb[6] & 0x02: err.append('Fehler: KESSELFUEHLER')
    if rb[6] & 0x04: err.append('Fehler: ZUS.-FUEHLER')
    if rb[6] & 0x08: err.append('Fehler: KESSEL KALT')
    if rb[6] & 0x10: err.append('Fehler: ABGAS-FUEHLER')
    if rb[6] & 0x20: err.append('Fehler: ABGAS-GRENZTEMPERATUR')
    if rb[6] & 0x40: err.append('Fehler: Sicherheitskette hat abgeschaltet')
    if rb[6] & 0x80: err.append('Externe Fehlermeldung')
    if rb[34] & 0x20: err.append('Betriebsartschalter: AUS')
    if rb[34] & 0x40: err.append('Betriebsartschalter: MANUELL')

    # Status flags
    st = []
    if rb[7] & 0x01: st.append('Abgastest')
    if rb[7] & 0x02: st.append('Stufe 1')
    if rb[7] & 0x04: st.append('Kesselschutz')
    if rb[7] & 0x08: st.append('Betrieb')
    if rb[7] & 0x10: st.append('Leistung frei')
    if rb[7] & 0x20: st.append('Leistung hoch')
    if rb[7] & 0x40: st.append('Stufe 2')
    if rb[34] & 0x01: st.append('Abgastest')
    if rb[34] & 0x02: st.append('Brenner=0')
    if rb[34] & 0x04: st.append('Brenner=Auto')
    if rb[34] & 0x08: st.append('Brenner=1')
    if rb[34] & 0x10: st.append('Brenner=2')

    err_str = f'FEHLER: {"; ".join(err)}' if err else ''
    logger.info('Kessel:      Soll/Ist = %d/%d C  Ein/Aus = %d/%d C  Brenner = %d  %s %s',
                ks, ki, k1, k0, br, ' '.join(st), err_str)

    if run % 2 == 0:
        send_data({'kessel': ki, 'kessel_s': ks, 'brenner': br, 'k_ein': k1, 'k_aus': k0,
                   'kessel_err': '; '.join(err)})


def decode_config(record: bytes):
    run = run_counters.get('config', 0)
    run_counters['config'] = run + 1

    rb = list(record)
    if not reclen('config', -18, len(record)):
        return

    at1 = signed_byte(rb[0])
    at2 = signed_byte(rb[1])

    err = ''
    if at1 == 110:
        at1 = None
        err = 'Aussentemperatur-Sensor defekt'

    if at1 is not None and (at1 < -40 or at1 > 50):
        return
    if at2 < -40 or at2 > 50:
        return

    if at1 is not None:
        logger.info('Aussen:      %d C (Gedaempft %d C)  %s', at1, at2, err)
    else:
        logger.info('Aussen:      -- C (Gedaempft %d C)  %s', at2, err)

    if run % 5 == 0 and at1 is not None:
        send_data({'aussen': at1, 'aussen_d': at2, 'aussen_err': err})


def decode_energy(record: bytes):
    run = run_counters.get('energy', 0)
    run_counters['energy'] = run + 1

    rb = list(record)
    if not reclen('energy', 36, len(record)):
        return

    wm = ((rb[30] * 256 + rb[31]) * 256 + rb[32]) * 256 + rb[33]
    sy = rb[2] + 1900
    sm = rb[1]
    sd = rb[0]

    logger.info('Energie:     %d Pulse seit %d-%02d-%02d', wm, sy, sm, sd)

    if run % 13 == 0:
        send_data({'energie': wm})


def decode_solar(record: bytes):
    run = run_counters.get('solar', 0)
    run_counters['solar'] = run + 1

    rb = list(record)
    if not reclen('solar', -10, len(record)):
        return

    ct = (rb[3] * 256 + rb[4]) / 10.0
    pu = rb[5]
    t1 = rb[6]
    t2 = rb[8]

    # Error flags
    err = []
    if rb[0] & 0x01: err.append('Hyst Error')
    if rb[0] & 0x02: err.append('Tank 2 Temp Limit')
    if rb[0] & 0x04: err.append('Tank 1 Temp Limit')
    if rb[0] & 0x08: err.append('Collector Temp Limit')

    # Status flags
    st = []
    if rb[7] & 0x01: st.append('T1 Off')
    if rb[7] & 0x02: st.append('T1 Low Solar')
    if rb[7] & 0x04: st.append('T1 Low Flow')
    if rb[7] & 0x08: st.append('T1 High Flow')
    if rb[7] & 0x10: st.append('T1 Manual')
    if rb[9] & 0x01: st.append('T2 Off')
    if rb[9] & 0x02: st.append('T2 Low Solar')
    if rb[9] & 0x04: st.append('T2 Low Flow')
    if rb[9] & 0x08: st.append('T2 High Flow')
    if rb[9] & 0x10: st.append('T2 Manual')

    err_str = f'ERROR: {"; ".join(err)}' if err else ''
    logger.info('Solar: Collector=%.1f C  Pump=%d  Tank1=%d  Tank2=%d  %s %s',
                ct, pu, t1, t2, ' '.join(st), err_str)

    send_data({'sol_coll': ct, 'sol_t1': t1, 'sol_t2': t2, 'sol_pump': pu,
               'sol_err': '; '.join(err)})


# --- Publishing ---

def send_data(params: dict[str, int | float | str]):
    topic_root = config.mqtt_topic
    for key, value in params.items():
        topic = f'{topic_root}/{key}'
        iot_daemonize.mqtt_client.publish(topic, value)
    logger.debug('[DATA] %s', ' '.join(f'{k}:{v}' for k, v in params.items()))


# --- Serial loop (daemon task) ---

def serial_loop(stop):
    try:
        _serial_loop(stop)
    except Exception:
        logger.exception('serial_loop crashed with unhandled exception')


MAX_BUF = 2048


def find_frame_marker(buf: bytearray) -> tuple[int, bool]:
    """Find the first 0xAF 0x82 / 0xAF 0x02 marker preceded by a checksum-valid frame.

    Validating the checksum keeps corrupted bytes before a marker from shifting
    frame boundaries. Returns (index of 0xAF or -1, alt_marker).
    """
    for i in range(9, len(buf) - 1):
        if buf[i] != 0xAF or buf[i + 1] not in (0x82, 0x02):
            continue
        if checksum(buf[i - 9:i - 1]) != buf[i - 1]:
            continue
        return i, buf[i + 1] == 0x02
    return -1, False


class FrameParser:
    """Assembles records from the raw serial byte stream and hands them to on_record."""

    def __init__(self, on_record):
        self.on_record = on_record
        self.buf = bytearray()
        self.lastrec = 0
        self.recbuf = bytearray()
        self.prev_af = False
        self.junk = bytearray()
        self.stats = {'blocks': 0, 'records': 0, 'lost_frames': 0}

    def _emit(self):
        if self.lastrec and len(self.recbuf):
            self.on_record(self.lastrec, bytes(self.recbuf))
            self.stats['records'] += 1

    def feed(self, chunk: bytes):
        for b in chunk:
            # The controller stuffs 0x00 after every 0xAF inside a frame, so data
            # never looks like an 0xAF 0x82 / 0xAF 0x02 end marker.
            if self.prev_af and b == 0x00:
                self.prev_af = False
                continue
            self.prev_af = b == 0xAF
            self.buf.append(b)
        if len(self.buf) > MAX_BUF:
            self.buf = self.buf[-MAX_BUF:]

        while True:
            be, alt_marker = find_frame_marker(self.buf)

            if be < 0:
                # A frame completed by future bytes needs at most the last 10 bytes here.
                if len(self.buf) > 10:
                    self.junk.extend(self.buf[:-10])
                    self.buf = self.buf[-10:]
                break

            self.junk.extend(self.buf[:be - 9])
            subblock = bytes(self.buf[be - 9:be])
            self.buf = self.buf[be + 1:]

            recnum = subblock[0]
            payofs = subblock[1]
            payload = subblock[2:8]

            # Only discards that lost a frame (payload offset gap) matter. The controller
            # sends a short non-frame block at the end of each cycle, which is harmless.
            expected_ofs = len(self.recbuf) if recnum == self.lastrec else 0
            if len(self.junk) > 1 and payofs != expected_ofs:
                self.stats['lost_frames'] += 1
                logger.warning('Lost frame before 0x%02x:%02x (expected offset 0x%02x), discarded %s',
                               recnum, payofs, expected_ofs, self.junk.hex())
            self.junk = bytearray()

            logger.debug('Block 0x%02x:%02d = %s', recnum, payofs, subblock.hex())
            self.stats['blocks'] += 1

            if payofs == 0 or alt_marker:
                self._emit()
                self.recbuf = bytearray(payload)
            elif recnum != self.lastrec:
                # Record type changed without payofs=0 (lost frame). Emit the
                # accumulated record and reset to prevent hybrid records.
                self._emit()
                self.recbuf = bytearray()
            elif len(self.recbuf):
                self.recbuf.extend(payload)

            self.lastrec = recnum

    def flush(self):
        self._emit()
        self.recbuf = bytearray()


def _serial_loop(stop):
    port = config.serial_port
    baud = config.serial_baud

    # Pre-configure terminal like the original Perl script
    try:
        subprocess.run(['stty', '-F', port, str(baud), 'cs8', '-cstopb', '-parity', 'raw', 'pass8'],
                       check=True, capture_output=True)
        logger.info('stty configured %s at %d bps', port, baud)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.warning('stty pre-configuration failed: %s', e)

    logger.info('Opening serial port %s at %d bps', port, baud)
    ser = serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.5,
    )

    try:
        parser = FrameParser(decode)
        last_heartbeat = time.monotonic()
        rx_bytes = 0
        hex_sample = bytearray()

        while not stop():
            chunk = ser.read(132)

            now = time.monotonic()
            if now - last_heartbeat >= 60:
                logger.info('serial_loop heartbeat: %d bytes rx, %d blocks ok, %d records, '
                            '%d lost frames, buf=%d',
                            rx_bytes, parser.stats['blocks'], parser.stats['records'],
                            parser.stats['lost_frames'], len(parser.buf))
                last_heartbeat = now

            if not chunk:
                continue
            rx_bytes += len(chunk)

            if len(hex_sample) < 200:
                hex_sample.extend(chunk[:200 - len(hex_sample)])
                if len(hex_sample) == 200:
                    logger.info('serial hex sample (first 200 bytes): %s', hex_sample.hex())

            parser.feed(chunk)

        parser.flush()
    finally:
        ser.close()
        logger.info('Serial port closed')
