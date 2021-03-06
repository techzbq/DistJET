import logging
import os

#FORMAT = '%(asctime)s: %(message)s'

#logging.basicConfig(level=logging.DEBUG, format=FORMAT)

def getLogger(name, level=logging.INFO):
    try:
        level = os.environ['DistJET_LOG_LEVEL']
    except:
        pass
    format = logging.Formatter('[%(asctime)s] %(threadName)s %(levelname)s: %(message)s')
    handler = logging.FileHandler('DistJET.'+name+'.log')
    handler.setFormatter(format)

    logger = logging.getLogger('DistJET.'+name)
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger

def setlevel(level, logger=None):
    os.environ['DistJET_LOG_LEVEL'] = level
