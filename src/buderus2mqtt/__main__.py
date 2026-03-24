#!/usr/bin/env python

import os

import iot_daemonize
import iot_daemonize.configuration as configuration

from . import daemon


def create_config():
    """Create MqttDaemonConfiguration with buderus-specific args."""
    cfg = configuration.MqttDaemonConfiguration(
        program='buderus2mqtt',
        description='Read Buderus Logamatic 4000 serial data and publish to MQTT'
    )

    # MQTT settings
    cfg.add_config_arg('mqtt_clientid', flags='--mqtt_clientid', default='buderus2mqtt',
                       help='The clientid to send to the MQTT server. Default is buderus2mqtt.')
    cfg.add_config_arg('mqtt_topic', flags='--mqtt_topic', default='heating',
                       help='The MQTT topic root. Default is heating.')

    # Config file
    cfg.add_config_arg('config', flags=['-c', '--config'], default='/etc/buderus2mqtt.conf',
                       help='The path to the config file. Default is /etc/buderus2mqtt.conf.')

    # Serial settings
    cfg.add_config_arg('serial_port', flags='--serial_port', default='/dev/ttyAMA0',
                       help='The serial port device. Default is /dev/ttyAMA0.')
    cfg.add_config_arg('serial_baud', flags='--serial_baud', default=1200,
                       help='The serial baud rate. Default is 1200.')

    cfg.parse_args()
    return cfg


def coerce_config_types(cfg):
    """Convert string config values to proper types after parsing."""
    int_keys = ['serial_baud']

    for key in int_keys:
        val = getattr(cfg, key, None)
        if val is not None:
            cfg._config_values[key] = int(val)


def main():
    config = create_config()

    if config.config and os.path.isfile(config.config):
        config.parse_config(config.config)

    coerce_config_types(config)

    daemon.config = config
    daemon.init_record_handlers()

    iot_daemonize.init(config, mqtt=True, http=False, daemonize=True)
    iot_daemonize.daemon.add_task(daemon.serial_loop)
    iot_daemonize.run()


if __name__ == '__main__':
    main()
