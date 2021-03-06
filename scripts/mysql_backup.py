#!/usr/bin/env python
# autobackup-dcm: Simple python script to autobackup, rotate backup and upload to google drive.
# this script create one daily incremental backups, and one full backup each week, month and year
#
# backups should be created and named as: app_name_DATE_HOUR_BACKTYPE.EXTENSION for example:
# jom_2015-12-25_09-58_daily.gz
# Author: dacopanCM <dacopan.bsc@gmail.com>
# URL: https://github.com/dacopan/autobackup-dcm

# Standard library modules.
import datetime
import json
import logging.config
import os
import time

# External dependencies.

from humanfriendly import format_path, Timer
from executor import execute, ExternalCommandFailed

# Modules included in our package.
from rotate_dcm import RotateBackupsCM
from gdrive_dcm import GDriveCM

# Semi-standard module version.
__version__ = '1.0'

# Initialize a logger for this module.
with open('../config/logging.json', 'rt') as f:
    config = json.load(f)
    logging.config.dictConfig(config)

log = logging.getLogger('dacopancm.mysql')

# constants
CONFIG_FILE = '../config/mysql_config.json'


def read_config():
    """Read configuration of app to backup from json file defined by `~CONFIG_FILE`

    :return: :class:`list` with all apps to backup loaded from json
    """
    global CONFIG_FILE
    with open(CONFIG_FILE, 'r') as f:
        cfg = json.load(f)
        return cfg


def save_last_backup_datetime(app, cfg):
    """
    Save in the app config file the timestamp of the last successful backup

    :param app: :class:`dict` with configuration returned by :func:`read_config()` modified by
    :param cfg: the modified version of cfg returned by :func:`~read_config()`.
    """
    log.info("starting save_last_backup_datetime to '{}'".format(app['cfg']['app_name']))

    filestamp = time.strftime('%Y-%m-%d_%H-%M')
    global CONFIG_FILE
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2, sort_keys=False)

    log.info("finish save_last_backup_datetime to '{}': {}".format(app['cfg']['app_name'], filestamp))


def rotate_backups(app):
    """ Rotate backups in local and remote directories of this app

    :param app: :class:`dict` with configuration returned by :func:`read_config()`
    """
    log.info("starting rotate_backups to '{}'".format(app['cfg']['app_name']))

    RotateBackupsCM(
        rotation_scheme=app['rotate']['local'],
        include_list=app['rotate']['include_list'],
        exclude_list=app['rotate']['exclude_list'],
        dry_run=app['rotate']['dry_run'],
        io_scheduling_class=app['rotate']['ionice'],
        rotate_type='local'
    ).rotate_backups(app['cfg']['local_backup_dir'])

    RotateBackupsCM(
        rotation_scheme=app['rotate']['remote'],
        include_list=app['rotate']['include_list'],
        exclude_list=app['rotate']['exclude_list'],
        dry_run=app['rotate']['dry_run'],
        io_scheduling_class=app['rotate']['ionice'],
        rotate_type='remote',
        gdrivecm=GDriveCM(google_credentials_name=app['cfg']['google_credentials_name'],
                          google_authorized=app['cfg']['google_authorized'],
                          remote_folder=app['cfg']['remote_backup_dir'])
    ).rotate_backups(app['cfg']['remote_backup_dir'])

    log.info("finish rotate_backups to '{}'".format(app['cfg']['app_name']))


def upload_backup(app, backup_file):
    """ Upload backup_file to Google Drive

    :param app: :class:`dict` with configuration returned by :func:`read_config()`
    :param backup_file: the local path of backup_file to upload
    :return: True if upload otherwise False
    """
    log.debug("uploading %s", backup_file)
    try:
        res = GDriveCM(google_credentials_name=app['cfg']['google_credentials_name'],
                       google_authorized=app['cfg']['google_authorized'],
                       remote_folder=app['cfg']['remote_backup_dir']
                       ).upload_file(backup_file)

        if res:
            log.info("uploaded %s", backup_file)
        else:
            log.info("error uploading %s", backup_file)

        return res

    except:
        log.error("Error uploading %s", backup_file)
        return False


def do_backup():
    """
    The magic occurs here

    """
    log.info('starting all backups')
    cfg = read_config()

    # get current time to determinate type of backup
    now = datetime.datetime.now()
    current_year = now.year
    current_month = now.month
    current_week = now.isocalendar()[1]
    current_day = now.day

    for app in cfg:
        rotate = False
        # now determine type of backup and run it
        if current_year > app['bk']['last_year']:
            backup_created = create_full_backup(app, 'yearly')  # now create backup to current app
            # if yearly full backup was created so not need create full backup of this month and week and daily
            if backup_created:
                app['bk']['last_year'] = current_year
                app['bk']['last_month'] = current_month
                app['bk']['last_week'] = current_week
                app['bk']['last_day'] = current_day
                rotate = True

        elif current_month > app['bk']['last_month']:
            backup_created = create_full_backup(app, 'monthly')  # now create backup to current app
            # if monthly full backup was created so not need create full backup of this week and daily
            if backup_created:
                app['bk']['last_month'] = current_month
                app['bk']['last_week'] = current_week
                app['bk']['last_day'] = current_day
                rotate = True

        elif current_week > app['bk']['last_week']:
            backup_created = create_full_backup(app, 'weekly')  # now create backup to current app
            # if weekly full backup was created so not need create daily backup of this day
            if backup_created:
                app['bk']['last_week'] = current_week
                app['bk']['last_day'] = current_day
                rotate = True

        elif current_day > app['bk']['last_day']:
            backup_created = create_full_backup(app, 'daily')  # now create backup to current app
            if backup_created:
                app['bk']['last_day'] = current_day
                rotate = True

        if rotate:
            rotate_backups(app)  # now rotate backups after backup created and uploaded
            #  if all are correctly now update config file to save the last backup created
            save_last_backup_datetime(app, cfg)

        else:
            log.info("No rotate all backups to '{}' up to date".format(app['cfg']['app_name']))

        log.info('end backups to \'{}\''.format(app['cfg']['app_name']))

    log.info('end all backups')


def create_full_backup(app, backup_type):
    """Create a full backup of a database defined in attr:´app´

    :param app: :class:`dict` with configuration returned by :func:`read_config()`
    :param backup_type: the key backup type to include in backup filename: daily, weekly, monthly, yearly
    :return: ``True`` if local and remote backup created correctly, ``False`` otherwise
    """
    log.info("starting full backup_{} to '{}'".format(backup_type, app['cfg']['app_name']))

    filestamp = time.strftime('%Y-%m-%d_%H-%M')
    # filestamp = '2016-03-28_09-17'
    backup_file = '{}{}_{}_{}.{}'.format(app['cfg']['local_backup_dir'], app['cfg']['app_name'], filestamp, backup_type,
                                         'gz')

    # here create backup


    try:
        os.makedirs(os.path.dirname(backup_file), exist_ok=True)
        timer = Timer()

        mysql_cmd = '/opt/lamp/mysql/bin/mysqldump '
        mysql_cmd += '--opt --triggers --events --user={} --password={} --databases {}'.format(
            app['custom']['db_user'], app['custom']['db_password'], app['custom']['db_name'])

        gzip_cmd = 'gzip -c > {}'.format(format_path(backup_file))

        mysql_cmd_res = execute(mysql_cmd, logger=log, capture=True)
        cmd_result = execute(gzip_cmd, logger=log, input=mysql_cmd_res, error_message='error en gzip')

        if cmd_result:
            log.info(
                "finish full backup_{} to '{}:{} in {}'".format(backup_type, app['cfg']['app_name'],
                                                                format_path(backup_file), timer))
            cmd_result = upload_backup(app, backup_file)
        else:
            log.error("error creating full backup_{} to '{}'".format(backup_type, app['cfg']['app_name']))
        return cmd_result
    except ExternalCommandFailed as ex:
        log.error(
            "error creating full backup_{} to '{} :{}'".format(backup_type, app['cfg']['app_name'], ex.error_message))


def create_incremental_backup(app, backup_type):
    """Create a incremental backup of a database defined in attr:´app´

        :param app: :class:`dict` with configuration returned by :func:`read_config()`
        :param backup_type: the key backup type to include in backup filename: daily, weekly, monthly, yearly
        :return: ``True`` if local and remote backup created correctly, ``False`` otherwise
    """

    log.info("starting incremental backup_{} to '{}'".format(backup_type, app['cfg']['app_name']))

    filestamp = time.strftime('%Y-%m-%d_%H-%M')
    backup_file = '{}{}_{}_{}.{}'.format(app['cfg']['local_backup_dir'], app['cfg']['app_name'], filestamp, backup_type,
                                         'gz')
    # here create incremental backup

    upload_backup(app, backup_file)

    log.info("finish incremental backup_{} to '{}:{}'".format(backup_type, app['cfg']['app_name'], backup_file))


if __name__ == "__main__":
    do_backup()
