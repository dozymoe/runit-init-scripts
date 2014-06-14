#!/usr/bin/env python

from dateutil.parser import parse as iso2datetime
import datetime
import logging
import os
from logging.handlers import SysLogHandler
from yaml import safe_load as yaml_load

FORCE_STOP_TIMEOUT = 10*60 # seconds
MAXIMUM_CRASH_HISTORY = 100

MAXIMUM_CRASHES = 5
MAXIMUM_CRASHES_PERIODE = 60 # seconds
MAXIMUM_CRASHES_DELAY   = 15*60 # seconds

SERVICES_META_FILE = '/var/service/schema.yml'
SERVICES_PATH = '/services'
CRASH_LOG_PATH = '/run/runit/crashes'

def check_crash_quota(name, current_time=None):
    if current_time is None:
        current_time = datetime.datetime.now()
    crashes = get_recent_crashes(name, current_time)
    return len(crashes) > MAXIMUM_CRASHES

def check_dependencies(name, log):
    bail = False

    # dependency check
    for s in get_needed_services(name):
        if not s.startswith('/'):
            s = os.path.join(SERVICES_PATH, s)
        with os.popen('sv status ' + s) as f:
            output = f.readline()
        output = output[:output.index(':')]
        if output == 'fail':
            log.error('missing dependency %s' % s)
            exit(1)
        elif output == 'down':
            bail = True
	    log.debug('starting dependency %s' % s)
            os.system('sv up %s' % s)

    if bail: exit(0)

    # make sure to run after these
    for s in get_before_services(name):
        if not s.startswith('/'):
            s = os.path.join(SERVICES_PATH, s)
        with os.popen('sv status ' + s) as f:
            output = f.readline()
        output = output[:output.index(':')]
        if output == 'fail':
            continue
        elif output == 'down':
            log.debug('waiting for %s' % s)
            exit(0)

def get_before_services(name):
    if not os.path.exists(SERVICES_META_FILE):  return []
    with open(SERVICES_META_FILE) as f:  data = yaml_load(f)
    return (s for s in data if 'after' in data[s] and name in data[s]['after'])
    
def get_dependant_services(name):
    if not os.path.exists(SERVICES_META_FILE):  return []
    with open(SERVICES_META_FILE) as f:  data = yaml_load(f)
    return (s for s in data if 'depend' in data[s] and name in data[s]['depend'])

def get_needed_services(name):
    if not os.path.exists(SERVICES_META_FILE):  return []
    with open(SERVICES_META_FILE) as f:  data = yaml_load(f)
    d = data.get(name, None)
    if d is None:  return []
    return d.get('depend', [])

def get_logger(name, level):
    syslog = SysLogHandler(address='/dev/log')
    formatter = logging.Formatter('%(name)s: %(levelname)s: %(message)s')
    syslog.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.addHandler(syslog)
    logger.setLevel(level)
    return logger

def get_recent_crashes(name, current_time):
    if not os.path.exists(CRASH_LOG_PATH):
        os.makedirs(CRASH_LOG_PATH)
    crash_log_filename = os.path.join(CRASH_LOG_PATH, name)
    if os.path.exists(crash_log_filename):
        with open(crash_log_filename) as f:
            # normalize date string into something python can use
            data = [iso2datetime(l.strip()) for l in f.readlines()]
            data.sort(reverse=True)
    else:
        data = []
    pruned_data = []
    for d in data:
        try:
            if (current_time - d).seconds <= MAXIMUM_CRASHES_PERIODE:
                pruned_data.append(d)
        except OverflowError:
            break
    return pruned_data

def run(bin, args, user=None, group=None, redirect=True):
    if redirect:
        STDOUT_FILENO = 1
        STDERR_FILENO = 2
        os.dup2(STDOUT_FILENO, STDERR_FILENO)

    binargs = []
    if (user and user != 'root') or (group and group != 'root'):
        if user:
	    u = user
	else:
	    u = 'root'
	if group:
	    u = u + ':' + group
        binargs.append('/usr/bin/chpst')
	binargs.append('-u')
	binargs.append(u)
    binargs.append(bin)
    binargs += args
    os.execv(binargs[0], binargs)

def update_crash_log(name):
    if not os.path.exists(CRASH_LOG_PATH):
        os.makedirs(CRASH_LOG_PATH)
    crash_log_filename = os.path.join(CRASH_LOG_PATH, name)
    if os.path.exists(crash_log_filename):
        with open(crash_log_filename) as f:
            data = [l.strip() for l in f.readlines()]
    else:
        data = []
    current_time = datetime.datetime.now()
    data.append(current_time.isoformat())
    while len(data) > MAXIMUM_CRASH_HISTORY:
        data.pop(0)
    with open(crash_log_filename, 'w') as f:
        for d in data:
            f.write("%s\n" % d)

def stop_dependant_services(name, log):
    for s in get_dependant_services(name):
        if not s.startswith('/'):
            s = os.path.join(SERVICES_PATH, s)
        log.debug('stopping dependant %s' % s)
        os.system('sv -w{time} force-stop {service}'.format(
            service=s,
            time=FORCED_STOP_TIMEOUT,
        ))
        os.system('sv exit {service}'.format(service=s))
