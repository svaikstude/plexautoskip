import configparser
from http import server
import os
import logging
import sys
import json
from resources.customEntries import CustomEntries
from enum import Enum
from plexapi.server import PlexServer


class FancyConfigParser(configparser.ConfigParser, object):
    def getlist(self, section, option, vars=None, separator=",", default=[], lower=True, replace=[' '], modifier=None):
        value = self.get(section, option, vars=vars)

        if not isinstance(value, str) and isinstance(value, list):
            return value

        if value == '':
            return list(default)

        value = value.split(separator)

        for r in replace:
            value = [x.replace(r, '') for x in value]
        if lower:
            value = [x.lower() for x in value]

        value = [x.strip() for x in value]

        if modifier:
            value = [modifier(x) for x in value]
        return value


class Settings:
    CONFIG_DEFAULT = "config.ini"
    CUSTOM_DEFAULT = "custom.json"
    CONFIG_DIRECTORY = "./config"
    RESOURCE_DIRECTORY = "./resources"
    RELATIVE_TO_ROOT = "../"
    ENV_CONFIG_VAR = "PAS_CONFIG"

    @property
    def CONFIG_RELATIVEPATH(self) -> str:
        return os.path.join(self.CONFIG_DIRECTORY, self.CONFIG_DEFAULT)

    DEFAULTS = {
        "Plex.tv": {
            "username": "",
            "password": "",
            "token": "",
            "servername": "",
        },
        "Server": {
            "address": "",
            "ssl": True,
            "port": 32400,
        },
        "Security": {
            "ignore-certs": False
        },
        "Skip": {
            "tags": "intro, commercial, advertisement",
            "last-chapter": 0.0,
            "unwatched": True,
            "first-episode-series": "Watched",
            "first-episode-season": "Always",
            "custom-cascade": True,
        },
        "Offsets": {
            "start": 3000,
            "end": 1000
        }
    }

    CUSTOM_DEFAULTS = {
        "markers": {},
        "offsets": {},
        "allowed": {
            'users': [],
            'clients': [],
            'keys': []
        },
        "blocked": {
            'users': [],
            'clients': [],
            'keys': []
        },
        "clients": {}
    }

    class SKIP_TYPES(Enum):
        NEVER = 0
        WATCHED = 1
        ALWAYS = 2

    SKIP_MATCHER = {
        "never": SKIP_TYPES.NEVER,
        "watched": SKIP_TYPES.WATCHED,
        "played": SKIP_TYPES.WATCHED,
        "always": SKIP_TYPES.ALWAYS,
        "all": SKIP_TYPES.ALWAYS,
        "true": SKIP_TYPES.ALWAYS,
        "false": SKIP_TYPES.NEVER,
        True: SKIP_TYPES.ALWAYS,
        False: SKIP_TYPES.NEVER
    }

    def __init__(self, configFile: str = None, logger: logging.Logger = None) -> None:
        self.log: logging.Logger = logger or logging.getLogger(__name__)

        self.username: str = None
        self.password: str = None
        self.servername: str = None
        self.token: str = None
        self.address: str = None
        self.ssl: bool = False
        self.port: int = 32400
        self.ignore_certs: bool = False
        self.cascade: bool = True
        self.leftOffset: int = 0
        self.rightOffset: int = 0
        self.customEntries: CustomEntries = None

        self._configFile: str = None

        self.log.info(sys.executable)
        if sys.version_info.major == 2:
            self.log.warning("Python 2 is not officially supported, use with caution")

        rootpath = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), self.RELATIVE_TO_ROOT))

        defaultConfigFile = os.path.normpath(os.path.join(rootpath, self.CONFIG_RELATIVEPATH))
        envConfigFile = os.environ.get(self.ENV_CONFIG_VAR)

        if envConfigFile and os.path.exists(os.path.realpath(envConfigFile)):
            configFile = os.path.realpath(envConfigFile)
            self.log.debug("%s environment variable override found." % (self.ENV_CONFIG_VAR))
        elif not configFile:
            configFile = defaultConfigFile
            self.log.debug("Loading default config file.")

        if os.path.isdir(configFile):
            configFile = os.path.realpath(os.path.join(configFile, self.CONFIG_RELATIVEPATH))
            self.log.debug("Configuration file specified is a directory, joining with %s." % (self.CONFIG_DEFAULT))

        self.log.info("Loading config file %s." % configFile)

        config: FancyConfigParser = FancyConfigParser()
        if os.path.isfile(configFile):
            config.read(configFile)

        write = False
        # Make sure all sections and all keys for each section are present
        for s in self.DEFAULTS:
            if not config.has_section(s):
                config.add_section(s)
                write = True
            for k in self.DEFAULTS[s]:
                if not config.has_option(s, k):
                    config.set(s, k, str(self.DEFAULTS[s][k]))
                    write = True
        if write:
            Settings.writeConfig(config, configFile)
        self._configFile = configFile

        self.readConfig(config)

        data = {}
        prefix, ext = os.path.splitext(self.CUSTOM_DEFAULT)
        for f in os.listdir(os.path.dirname(configFile)):
            fullpath = os.path.join(os.path.dirname(configFile), f)
            if os.path.isfile(fullpath) and f.startswith(prefix) and f.endswith(ext):
                self.merge(data, self.loadCustom(fullpath))
            else:
                continue
        if not data:
            self.merge(data, self.loadCustom(os.path.join(os.path.dirname(configFile), self.CUSTOM_DEFAULT)))

        self.customEntries = CustomEntries(data, self.cascade, logger)

    def loadCustom(self, customFile: str) -> dict:
        data = dict(self.CUSTOM_DEFAULTS)
        if not os.path.exists(customFile):
            Settings.writeCustom(self.CUSTOM_DEFAULTS, customFile)
        elif os.path.exists(customFile):
            try:
                with open(customFile, encoding='utf-8') as f:
                    data = json.load(f)
            except:
                self.log.exception("Found custom file %s but failed to load, using defaults" % (customFile))

            write = False
            # Make sure default entries are present to prevent exceptions
            for k in self.CUSTOM_DEFAULTS:
                if k not in data:
                    data[k] = {}
                    write = True
                for sk in self.CUSTOM_DEFAULTS[k]:
                    if sk not in data[k]:
                        data[k][sk] = []
                        write = True
            if write:
                Settings.writeCustom(data, customFile)
        self.log.info("Loading custom JSON file %s" % customFile)
        return data

    def merge(self, d1: dict, d2: dict) -> None:
        for k in d2:
            if k in d1 and isinstance(d1[k], dict) and isinstance(d2[k], dict):
                self.merge(d1[k], d2[k])
            elif k in d1 and isinstance(d1[k], list) and isinstance(d2[k], list):
                d1[k].extend(d2[k])
            else:
                d1[k] = d2[k]

    @staticmethod
    def writeConfig(config: configparser.ConfigParser, cfgfile: str, logger: logging.Logger = None) -> None:
        log = logger or logging.getLogger(__name__)
        if not os.path.isdir(os.path.dirname(cfgfile)):
            os.makedirs(os.path.dirname(cfgfile))
        try:
            fp = open(cfgfile, "w")
            config.write(fp)
            fp.close()
        except PermissionError:
            log.exception("Error writing to %s due to permissions" % (cfgfile))
        except IOError:
            log.exception("Error writing to %s" % (cfgfile))

    @staticmethod
    def writeCustom(data: dict, cfgfile: str, logger: logging.Logger = None) -> None:
        log = logger or logging.getLogger(__name__)
        try:
            with open(cfgfile, 'w', encoding='utf-8') as cf:
                json.dump(data, cf, indent=4)
        except PermissionError:
            log.exception("Error writing to %s due to permissions" % (cfgfile))
        except IOError:
            log.exception("Error writing to %s" % (cfgfile))

    def readConfig(self, config: FancyConfigParser) -> None:
        self.username = config.get("Plex.tv", "username")
        self.password = config.get("Plex.tv", "password", raw=True)
        self.servername = config.get("Plex.tv", "servername")
        self.token = config.get("Plex.tv", "token", raw=True)

        self.address = config.get("Server", "address")
        for prefix in ['http://', 'https://']:
            if self.address.startswith(prefix):
                self.address = self.address[len(prefix):]
        while self.address.endswith("/"):
            self.address = self.address[:1]
        self.ssl = config.getboolean("Server", "ssl")
        self.port = config.getint("Server", "port")

        self.ignore_certs = config.getboolean("Security", "ignore-certs")

        self.tags = config.getlist("Skip", "tags")
        self.skipunwatched = config.getboolean("Skip", "unwatched")
        self.skiplastchapter = config.getfloat("Skip", "last-chapter")
        try:
            self.skipS01E01 = self.SKIP_MATCHER.get(config.getboolean("Skip", "first-episode-series"))  # Legacy bool support
        except ValueError:
            self.skipS01E01 = self.SKIP_MATCHER.get(config.get("Skip", "first-episode-series").lower(), self.SKIP_TYPES.ALWAYS)
        try:
            self.skipE01 = self.SKIP_MATCHER.get(config.getboolean("Skip", "first-episode-season"))  # Legacy bool support
        except ValueError:
            self.skipE01 = self.SKIP_MATCHER.get(config.get("Skip", "first-episode-season").lower(), self.SKIP_TYPES.ALWAYS)
        self.cascade = config.getboolean("Skip", "custom-cascade")

        self.leftOffset = config.getint("Offsets", "start")
        self.rightOffset = config.getint("Offsets", "end")

    def replaceWithGUIDs(self, server: PlexServer) -> None:
        ratingKeyLookup = self.customEntries.loadRatingKeys(server)
        prefix, ext = os.path.splitext(self.CUSTOM_DEFAULT)
        for f in os.listdir(os.path.dirname(self._configFile)):
            fullpath = os.path.join(os.path.dirname(self._configFile), f)
            if os.path.isfile(fullpath) and f.startswith(prefix) and f.endswith(ext):
                c = CustomEntries(self.loadCustom(fullpath), self.cascade, self.log)
                c.convertToGuids(server, ratingKeyLookup)
                Settings.writeCustom(c.data, fullpath)
            else:
                continue

    def replaceWithRatingKeys(self, server: PlexServer) -> None:
        guidLookup = self.customEntries.loadGuids(server)
        prefix, ext = os.path.splitext(self.CUSTOM_DEFAULT)
        for f in os.listdir(os.path.dirname(self._configFile)):
            fullpath = os.path.join(os.path.dirname(self._configFile), f)
            if os.path.isfile(fullpath) and f.startswith(prefix) and f.endswith(ext):
                c = CustomEntries(self.loadCustom(fullpath), self.cascade, self.log)
                c.convertToRatingKeys(server, guidLookup)
                Settings.writeCustom(c.data, fullpath, self.log)
            else:
                continue
