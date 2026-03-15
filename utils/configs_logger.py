import logging
from sys import stdout
from time import gmtime

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)
FORMATTER = logging.Formatter('%(asctime)s - %(name)s - %(funcName)s - %(levelname)s - %(message)s')
FORMATTER.converter = gmtime

sh = logging.StreamHandler(stream=stdout)
sh.setLevel(logging.DEBUG)
sh.setFormatter(FORMATTER)
LOGGER.addHandler(sh)
