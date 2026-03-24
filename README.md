# buderus2mqtt

Read data from a Buderus Logamatic 4000 series heating controller via serial interface and publish to MQTT.

Based on [l4000-daemon.pl](https://github.com/) v2.0 by Peter G. Holzleitner.

## Hardware

Requires a Raspberry Pi (or similar) serial interface attached to the Logamatic's room sensor interface via a level shifter (10 KOhm, 12 V Zener diode, transistor, pull-up resistor). Communication runs at 1200 bps, 8N1.

## Installation

### Docker

```sh
docker run -d --name buderus2mqtt \
    --device /dev/ttyAMA0 \
    -v /path/to/buderus2mqtt.conf:/etc/buderus2mqtt.conf \
    c0d3.sh/andre/buderus2mqtt:latest
```

### Native

```sh
./install
cp buderus2mqtt.conf.example buderus2mqtt.conf
# edit buderus2mqtt.conf
./run
```

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `mqtt_host` | `localhost` | MQTT broker host |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_clientid` | `buderus2mqtt` | MQTT client ID |
| `mqtt_user` | | MQTT username |
| `mqtt_password` | | MQTT password |
| `mqtt_topic` | `heating` | MQTT topic root |
| `serial_port` | `/dev/ttyAMA0` | Serial port device |
| `serial_baud` | `1200` | Serial baud rate |
| `verbose` | `false` | Enable verbose logging |

## MQTT Topics

All values are published under `{mqtt_topic}/{key}`:

| Key | Description |
|-----|-------------|
| `hk{1-9}` | Heating zone room temperature |
| `hk{1-9}_s` | Zone setpoint |
| `hk{1-9}_sg` | Zone actuator position |
| `hk{1-9}_v` | Zone flow temperature |
| `hk{1-9}_vs` | Zone flow setpoint |
| `hk{1-9}_pu` | Zone pump |
| `hk{1-9}_err` | Zone error |
| `ww` | Hot water temperature |
| `ww_s` | Hot water setpoint |
| `ww_l` | Hot water loading |
| `ww_laden` | Charge pump |
| `ww_zirk` | Circulation pump |
| `ww_err` | Hot water error |
| `kessel` | Boiler temperature |
| `kessel_s` | Boiler setpoint |
| `brenner` | Burner status |
| `k_ein` | Boiler on threshold |
| `k_aus` | Boiler off threshold |
| `kessel_err` | Boiler error |
| `aussen` | Outdoor temperature |
| `aussen_d` | Damped outdoor temperature |
| `aussen_err` | Outdoor sensor error |
| `energie` | Energy pulse counter |
| `sol_coll` | Solar collector temperature |
| `sol_t1` | Solar tank 1 temperature |
| `sol_t2` | Solar tank 2 temperature |
| `sol_pump` | Solar pump status |
| `sol_err` | Solar error |

## License

BSD-2-Clause — see [LICENSE.md](LICENSE.md)
