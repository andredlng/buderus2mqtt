import time
from unittest.mock import MagicMock, patch

import pytest

from buderus2mqtt.daemon import (
    checksum,
    signed_byte,
    reclen,
    decode_zone,
    decode_water,
    decode_boiler,
    decode_config,
    decode_energy,
    decode_solar,
    report_error,
    init_record_handlers,
    run_counters,
    error_states,
    send_data,
)
import buderus2mqtt.daemon as daemon


@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state between tests."""
    run_counters.clear()
    error_states.clear()
    # Provide a mock config
    daemon.config = MagicMock()
    daemon.config.mqtt_topic = 'heating'
    daemon.config.smtp_host = ''
    daemon.config.mail_repeat_seconds = 14400
    yield
    run_counters.clear()
    error_states.clear()


# --- Protocol helpers ---


class TestChecksum:
    def test_known_block_zone2(self):
        """Sample from Perl source: 81 00 04 02 28 28 2a 29 59"""
        block = [0x81, 0x00, 0x04, 0x02, 0x28, 0x28, 0x2A, 0x29]
        assert checksum(block) == 0x59

    def test_known_block_zone2_cont(self):
        """Sample: 81 06 00 00 64 02 80 00 d0"""
        block = [0x81, 0x06, 0x00, 0x00, 0x64, 0x02, 0x80, 0x00]
        assert checksum(block) == 0xD0

    def test_known_block_zone2_cont2(self):
        """Sample: 81 0c 2c 38 43 00 00 00 a4"""
        block = [0x81, 0x0C, 0x2C, 0x38, 0x43, 0x00, 0x00, 0x00]
        assert checksum(block) == 0xA4

    def test_known_block_zone3(self):
        """Sample: 82 00 04 02 19 19 26 6e 9c"""
        block = [0x82, 0x00, 0x04, 0x02, 0x19, 0x19, 0x26, 0x6E]
        assert checksum(block) == 0x9C

    def test_known_block_zone3_cont(self):
        """Sample: 82 06 00 00 64 00 80 00 93"""
        block = [0x82, 0x06, 0x00, 0x00, 0x64, 0x00, 0x80, 0x00]
        assert checksum(block) == 0x93

    def test_all_zeros(self):
        block = [0x00] * 8
        assert checksum(block) == 0x00


class TestSignedByte:
    def test_positive_values(self):
        assert signed_byte(0) == 0
        assert signed_byte(1) == 1
        assert signed_byte(127) == 127

    def test_negative_values(self):
        assert signed_byte(128) == -128
        assert signed_byte(255) == -1
        assert signed_byte(200) == -56

    def test_boundary(self):
        assert signed_byte(127) == 127
        assert signed_byte(128) == -128


class TestReclen:
    def test_exact_match(self):
        assert reclen('test', 18, 18) is True

    def test_exact_mismatch(self):
        assert reclen('test', 18, 12) is False

    def test_minimum_match(self):
        assert reclen('test', -18, 18) is True
        assert reclen('test', -18, 24) is True

    def test_minimum_too_short(self):
        assert reclen('test', -18, 12) is False

    def test_zero_expected_always_passes(self):
        assert reclen('test', 0, 42) is True


# --- Record decoders ---


def make_zone_record(rb0=0x04, rb1=0x02, vs=40, vi=40, rs_raw=42, ri_raw=41,
                     o1=0, o0=0, pu=64, sg=0, rb10=0, rb11=0,
                     k1=43, k2=38, k3=44):
    """Build an 18-byte zone record."""
    rec = [0] * 18
    rec[0] = rb0
    rec[1] = rb1
    rec[2] = vs
    rec[3] = vi
    rec[4] = rs_raw
    rec[5] = ri_raw
    rec[6] = o1
    rec[7] = o0
    rec[8] = pu
    rec[9] = sg & 0xFF
    rec[10] = rb10
    rec[11] = rb11
    rec[12] = k1
    rec[13] = k2
    rec[14] = k3
    return bytes(rec)


class TestDecodeZone:
    @patch('buderus2mqtt.daemon.send_data')
    def test_basic_zone1_publish(self, mock_send):
        """Zone 1 publishes on run 1 (odd zone, odd run)."""
        # run 0 -> zone 1: 0 % 2 == 1 % 2 -> 0 != 1, no publish
        # run 1 -> zone 1: 1 % 2 == 1 % 2 -> 1 == 1, publish
        record = make_zone_record(rs_raw=42, ri_raw=41, vs=40, vi=40, pu=64)
        decode_zone(1, record)  # run 0, won't publish
        decode_zone(1, record)  # run 1, will publish

        assert mock_send.call_count == 1
        data = mock_send.call_args[0][0]
        assert data['hk1'] == 41 / 2.0  # ri = 20.5
        assert data['hk1_s'] == 42 / 2.0  # rs = 21.0
        assert data['hk1_v'] == 40  # vi
        assert data['hk1_vs'] == 40  # vs
        assert data['hk1_pu'] == 64

    @patch('buderus2mqtt.daemon.send_data')
    def test_zone2_publishes_on_even_run(self, mock_send):
        """Zone 2 publishes on run 0 (even zone, even run)."""
        record = make_zone_record()
        decode_zone(2, record)  # run 0, 0 % 2 == 2 % 2 -> 0 == 0, publish
        assert mock_send.call_count == 1

    @patch('buderus2mqtt.daemon.send_data')
    def test_invalid_sensor_skips_ri_and_pu(self, mock_send):
        """rb[5]=110 (ri=55.0) marks invalid sensor: hk and hk_pu not published."""
        record = make_zone_record(ri_raw=110)
        decode_zone(2, record)  # run 0, publishes

        data = mock_send.call_args[0][0]
        assert 'hk2' not in data
        assert 'hk2_pu' not in data
        assert 'hk2_s' in data
        assert 'hk2_sg' in data
        assert 'hk2_v' in data
        assert 'hk2_vs' in data

    @patch('buderus2mqtt.daemon.send_data')
    def test_valid_sensor_at_27_5_publishes(self, mock_send):
        """rb[5]=55 (ri=27.5) is a valid room temp, should publish hk and hk_pu."""
        record = make_zone_record(ri_raw=55)
        decode_zone(2, record)  # run 0, publishes

        data = mock_send.call_args[0][0]
        assert data['hk2'] == 27.5
        assert 'hk2_pu' in data

    @patch('buderus2mqtt.daemon.send_data')
    def test_wrong_record_length(self, mock_send):
        """Short record should be rejected."""
        decode_zone(1, bytes(12))
        assert mock_send.call_count == 0

    @patch('buderus2mqtt.daemon.send_data')
    def test_signed_stellglied(self, mock_send):
        """Actuator position (sg) uses signed byte conversion."""
        record = make_zone_record(sg=200)  # -> signed_byte(200) = -56
        decode_zone(2, record)
        data = mock_send.call_args[0][0]
        assert data['hk2_sg'] == -56


def make_water_record(rb0=0x01, rb1=0x01, ws=60, wi=55, rb4=0, rb5=0x01, rb6=0):
    """Build a 12-byte water record."""
    rec = [0] * 12
    rec[0] = rb0
    rec[1] = rb1
    rec[2] = ws
    rec[3] = wi
    rec[4] = rb4
    rec[5] = rb5
    rec[6] = rb6
    return bytes(rec)


class TestDecodeWater:
    @patch('buderus2mqtt.daemon.send_data')
    def test_basic_publish(self, mock_send):
        """Water publishes on even run."""
        record = make_water_record(ws=60, wi=55, rb1=0x01, rb5=0x03)
        decode_water(record)  # run 0, publishes

        data = mock_send.call_args[0][0]
        assert data['ww'] == 55
        assert data['ww_s'] == 60
        assert data['ww_l'] == 1  # rb[1] & 0x01
        assert data['ww_laden'] == 1  # rb[5] & 0x01
        assert data['ww_zirk'] == 1  # rb[5] & 0x02

    @patch('buderus2mqtt.daemon.send_data')
    def test_odd_run_no_publish(self, mock_send):
        """Water does not publish on odd run."""
        record = make_water_record()
        decode_water(record)  # run 0, publishes
        decode_water(record)  # run 1, no publish
        assert mock_send.call_count == 1

    @patch('buderus2mqtt.daemon.send_data')
    def test_wrong_record_length(self, mock_send):
        decode_water(bytes(6))
        assert mock_send.call_count == 0


def make_boiler_record(ks=70, ki=65, k1=60, k0=55, br=80):
    """Build a 42-byte boiler record."""
    rec = [0] * 42
    rec[0] = ks
    rec[1] = ki
    rec[2] = k1
    rec[3] = k0
    rec[8] = br
    return bytes(rec)


class TestDecodeBoiler:
    @patch('buderus2mqtt.daemon.send_data')
    def test_basic_publish(self, mock_send):
        record = make_boiler_record(ks=70, ki=65, k1=60, k0=55, br=80)
        decode_boiler(record)  # run 0, publishes

        data = mock_send.call_args[0][0]
        assert data['kessel'] == 65
        assert data['kessel_s'] == 70
        assert data['brenner'] == 80
        assert data['k_ein'] == 60
        assert data['k_aus'] == 55

    @patch('buderus2mqtt.daemon.send_data')
    def test_odd_run_no_publish(self, mock_send):
        record = make_boiler_record()
        decode_boiler(record)  # run 0
        decode_boiler(record)  # run 1
        assert mock_send.call_count == 1

    @patch('buderus2mqtt.daemon.send_data')
    def test_wrong_record_length(self, mock_send):
        decode_boiler(bytes(18))
        assert mock_send.call_count == 0


def make_config_record(at1=10, at2=8):
    """Build an 18-byte config record."""
    rec = [0] * 18
    rec[0] = at1 & 0xFF
    rec[1] = at2 & 0xFF
    return bytes(rec)


class TestDecodeConfig:
    @patch('buderus2mqtt.daemon.send_data')
    @patch('buderus2mqtt.daemon.send_mail')
    def test_basic_publish(self, mock_mail, mock_send):
        """Config publishes on run % 5 == 0."""
        record = make_config_record(at1=10, at2=8)
        decode_config(record)  # run 0, publishes

        data = mock_send.call_args[0][0]
        assert data['aussen'] == 10
        assert data['aussen_d'] == 8

    @patch('buderus2mqtt.daemon.send_data')
    @patch('buderus2mqtt.daemon.send_mail')
    def test_negative_temperature(self, mock_mail, mock_send):
        """Negative temps via signed_byte."""
        record = make_config_record(at1=246, at2=248)  # -10, -8
        decode_config(record)

        data = mock_send.call_args[0][0]
        assert data['aussen'] == -10
        assert data['aussen_d'] == -8

    @patch('buderus2mqtt.daemon.send_data')
    @patch('buderus2mqtt.daemon.send_mail')
    def test_sensor_fault_no_publish(self, mock_mail, mock_send):
        """rb[0]=110 signals sensor fault, skips publishing."""
        record = make_config_record(at1=110, at2=8)
        decode_config(record)
        assert mock_send.call_count == 0

    @patch('buderus2mqtt.daemon.send_data')
    @patch('buderus2mqtt.daemon.send_mail')
    def test_plausibility_rejection(self, mock_mail, mock_send):
        """Temps outside -40..50 range are rejected."""
        record = make_config_record(at1=60, at2=8)  # 60 > 50
        decode_config(record)
        assert mock_send.call_count == 0

    @patch('buderus2mqtt.daemon.send_data')
    @patch('buderus2mqtt.daemon.send_mail')
    def test_startup_email_on_run1(self, mock_mail, mock_send):
        """Sends startup email on second call (run == 1)."""
        record = make_config_record(at1=10, at2=8)
        decode_config(record)  # run 0
        decode_config(record)  # run 1 -> startup mail
        assert mock_mail.call_count == 1
        assert 'Monitoring gestartet' in mock_mail.call_args[0][0]

    @patch('buderus2mqtt.daemon.send_data')
    @patch('buderus2mqtt.daemon.send_mail')
    def test_publish_frequency(self, mock_mail, mock_send):
        """Only publishes every 5th run."""
        record = make_config_record(at1=10, at2=8)
        for _ in range(10):
            decode_config(record)
        # Runs 0, 5 -> 2 publishes out of 10
        assert mock_send.call_count == 2


def make_energy_record(wm=12345, sd=15, sm=3, sy=126):
    """Build a 36-byte energy record. sy is offset from 1900."""
    rec = [0] * 36
    rec[0] = sd
    rec[1] = sm
    rec[2] = sy
    rec[30] = (wm >> 24) & 0xFF
    rec[31] = (wm >> 16) & 0xFF
    rec[32] = (wm >> 8) & 0xFF
    rec[33] = wm & 0xFF
    return bytes(rec)


class TestDecodeEnergy:
    @patch('buderus2mqtt.daemon.send_data')
    def test_basic_publish(self, mock_send):
        """Energy publishes on run % 13 == 0."""
        record = make_energy_record(wm=123456)
        decode_energy(record)  # run 0

        data = mock_send.call_args[0][0]
        assert data['energie'] == 123456

    @patch('buderus2mqtt.daemon.send_data')
    def test_32bit_counter(self, mock_send):
        """Verify 32-bit big-endian counter assembly."""
        wm = 0x01020304
        record = make_energy_record(wm=wm)
        decode_energy(record)

        data = mock_send.call_args[0][0]
        assert data['energie'] == 0x01020304

    @patch('buderus2mqtt.daemon.send_data')
    def test_publish_frequency(self, mock_send):
        """Only publishes every 13th run."""
        record = make_energy_record()
        for _ in range(26):
            decode_energy(record)
        # Runs 0, 13 -> 2 publishes
        assert mock_send.call_count == 2


def make_solar_record(ct_raw=350, pu=80, t1=45, t2=40, rb0=0, rb7=0, rb9=0):
    """Build a 10-byte solar record."""
    rec = [0] * 10
    rec[0] = rb0
    rec[3] = (ct_raw >> 8) & 0xFF
    rec[4] = ct_raw & 0xFF
    rec[5] = pu
    rec[6] = t1
    rec[7] = rb7
    rec[8] = t2
    rec[9] = rb9
    return bytes(rec)


class TestDecodeSolar:
    @patch('buderus2mqtt.daemon.send_data')
    def test_basic_publish(self, mock_send):
        """Solar publishes every run."""
        record = make_solar_record(ct_raw=350, pu=80, t1=45, t2=40)
        decode_solar(record)

        data = mock_send.call_args[0][0]
        assert data['sol_coll'] == 35.0  # 350 / 10.0
        assert data['sol_pump'] == 80
        assert data['sol_t1'] == 45
        assert data['sol_t2'] == 40

    @patch('buderus2mqtt.daemon.send_data')
    def test_always_publishes(self, mock_send):
        """Solar has no publish throttling."""
        record = make_solar_record()
        for _ in range(5):
            decode_solar(record)
        assert mock_send.call_count == 5


# --- Error tracking ---


class TestReportError:
    @patch('buderus2mqtt.daemon.send_mail')
    def test_new_error_sends_mail(self, mock_mail):
        report_error('Kessel', 'BRENNERSTOERUNG')
        assert mock_mail.call_count == 1
        assert 'FEHLER: Kessel' in mock_mail.call_args[0][0]
        assert 'Neuer Fehler' in mock_mail.call_args[0][1]

    @patch('buderus2mqtt.daemon.send_mail')
    def test_same_error_within_repeat_no_mail(self, mock_mail):
        report_error('Kessel', 'BRENNERSTOERUNG')
        mock_mail.reset_mock()
        report_error('Kessel', 'BRENNERSTOERUNG')
        assert mock_mail.call_count == 0

    @patch('buderus2mqtt.daemon.send_mail')
    def test_same_error_after_repeat_sends_reminder(self, mock_mail):
        report_error('Kessel', 'BRENNERSTOERUNG')
        # Simulate time passing beyond repeat interval
        error_states['Kessel']['lasttime'] = time.time() - 15000
        mock_mail.reset_mock()
        report_error('Kessel', 'BRENNERSTOERUNG')
        assert mock_mail.call_count == 1
        assert 'ERINNERUNG' in mock_mail.call_args[0][0]

    @patch('buderus2mqtt.daemon.send_mail')
    def test_error_cleared_sends_gutmeldung(self, mock_mail):
        report_error('Kessel', 'BRENNERSTOERUNG')
        mock_mail.reset_mock()
        report_error('Kessel', '')
        assert mock_mail.call_count == 1
        assert 'GUTMELDUNG' in mock_mail.call_args[0][0]

    @patch('buderus2mqtt.daemon.send_mail')
    def test_no_error_then_no_error_no_mail(self, mock_mail):
        report_error('Kessel', '')
        assert mock_mail.call_count == 0

    @patch('buderus2mqtt.daemon.send_mail')
    def test_different_error_sends_new_mail(self, mock_mail):
        report_error('Kessel', 'BRENNERSTOERUNG')
        mock_mail.reset_mock()
        report_error('Kessel', 'KESSELFUEHLER')
        assert mock_mail.call_count == 1


# --- Record handler registration ---


class TestRecordHandlers:
    def test_init_record_handlers(self):
        init_record_handlers()
        from buderus2mqtt.daemon import RECORD_HANDLERS
        assert 0x80 in RECORD_HANDLERS  # zone 1
        assert 0x8E in RECORD_HANDLERS  # zone 9
        assert 0x84 in RECORD_HANDLERS  # water
        assert 0x88 in RECORD_HANDLERS  # boiler
        assert 0x89 in RECORD_HANDLERS  # config
        assert 0x9B in RECORD_HANDLERS  # energy
        assert 0x9E in RECORD_HANDLERS  # solar
