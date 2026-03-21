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

    # SMTP settings
    cfg.add_config_arg('smtp_host', flags='--smtp_host', default='',
                       help='SMTP server for error email notifications. Empty to disable.')
    cfg.add_config_arg('smtp_port', flags='--smtp_port', default=587,
                       help='SMTP port. Default is 587.')
    cfg.add_config_arg('smtp_user', flags='--smtp_user', default='',
                       help='SMTP username for authentication.')
    cfg.add_config_arg('smtp_password', flags='--smtp_password', default='',
                       help='SMTP password for authentication.')
    cfg.add_config_arg('smtp_from', flags='--smtp_from', default='',
                       help='Email sender address.')
    cfg.add_config_arg('smtp_to', flags='--smtp_to', default='',
                       help='Email recipient address.')
    cfg.add_config_arg('mail_repeat_seconds', flags='--mail_repeat_seconds', default=14400,
                       help='Repeat error emails after this many seconds. Default is 14400 (4 hours).')

    cfg.parse_args()
    return cfg


def coerce_config_types(cfg):
    """Convert string config values to proper types after parsing."""
    int_keys = ['serial_baud', 'smtp_port', 'mail_repeat_seconds']

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
