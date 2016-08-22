import os.path
import shutil
import json
from pathlib import Path

from glob import glob
from zipfile import ZipFile
from urllib.parse import urljoin

import requests

from fac.files import JSONFile
from fac.utils import JSONDict
from fac.api import AuthError


class Mod:
    location = None

    def __init__(self, manager, location):
        self.manager = manager
        self.location = location

    def get_enabled(self):
        return self.manager.is_mod_enabled(self.name)

    def set_enabled(self, val):
        self.manager.set_mod_enabled(self.name, val)

    enabled = property(get_enabled, set_enabled)

    @property
    def name(self):
        return self.info.name

    @property
    def version(self):
        return self.info.version

    def _check_valid(self):
        expected_basename = "%s_%s" % (self.name, self.version)

        assert self.basename == expected_basename, \
            "Invalid file name %s, expected %s" % (
                    self.basename,
                    expected_basename
            )

    @classmethod
    def _find(cls, pattern, manager, name, version):
        name = name or '*'
        version = version or '*'

        files = glob(
            os.path.join(
                manager.config.mods_path,
                pattern % (name, version)
            )
        )
        for file in files:
            try:
                mod = cls(manager, file)
                yield mod
            except Exception as ex:
                print('Warning: invalid mod %s: %s' % (file, ex))


class ZippedMod(Mod):
    packed = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.basename = os.path.splitext(
                os.path.basename(
                    self.location
                )
        )[0]
        self._check_valid()

    def remove(self):
        print('Removing file: %s' % self.location)
        os.remove(self.location)

    @property
    def info(self):
        with ZipFile(self.location) as f:
            info = json.loads(
                    f.read(
                        '%s/info.json' % self.basename,
                        ).decode('utf-8'),
                    )
            return JSONDict(info)

    def unpack(self):
        mod_directory = self.manager.config.mods_path
        unpacked_location = os.path.join(mod_directory, self.basename)

        print('Unpacking: %s' % self.location)

        with ZipFile(self.location) as f:
            os.makedirs(unpacked_location)

            for info in f.infolist():
                if not info.filename.startswith(self.basename + '/'):
                    print("Warning: out-of-directory file %s ignored" % (
                        info.filename))
                    continue
                f.extract(info, mod_directory)

        self.remove()
        return UnpackedMod(self.manager, unpacked_location)

    @classmethod
    def find(cls, *args, **kwargs):
        return cls._find("%s_%s.zip", *args, **kwargs)


class UnpackedMod(Mod):
    packed = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.basename = os.path.basename(
            os.path.realpath(
                self.location
            )
        )
        self._check_valid()

    def remove(self):
        print('Removing directory: %s' % self.location)
        shutil.rmtree(self.location)

    @property
    def info(self):
        path = os.path.join(self.location, 'info.json')
        return JSONFile(path)

    def pack(self):
        packed_location = os.path.join(
            self.manager.config.mods_path,
            self.basename + '.zip'
        )

        print('Packing: %s' % self.location)

        if os.path.exists(packed_location):
            raise Exception("File already exists: %s" % packed_location)

        with ZipFile(packed_location, "w") as f:
            for root, dirs, files in os.walk(self.location):
                zip_root = Path(root).relative_to(
                    self.manager.config.mods_path).as_posix()

                for file_name in files:
                    f.write(
                        '%s/%s' % (root, file_name),
                        '%s/%s' % (zip_root, file_name),
                    )

        self.remove()

        return ZippedMod(self.manager, packed_location)

    @classmethod
    def find(cls, *args, **kwargs):
        return cls._find("%s_%s/", *args, **kwargs)


class ModManager:
    'Provides access to the factorio mods directory'

    def __init__(self, config, api):
        self.api = api
        self.config = config
        self.mods_json = JSONFile(
            os.path.join(
                self.config.mods_path,
                'mod-list.json'
            )
        )

    def get_mod_json(self, name):
        """Return the mod json configuration from mods-list.json"""

        for mod in self.mods_json.mods:
            if mod.name == name:
                return mod

    def get_mod(self, name):
        for mod in self.get_mods(name):
            if mod.name == name:
                return mod

    def get_mods(self, name=None, version=None):
        for mod_type in (ZippedMod, UnpackedMod):
            yield from mod_type.find(self, name, version)

    def resolve_remote_requirement(self, req):
        spec = req.specifier
        game_ver = self.config.game_version_major

        mod = self.api.get(req.name)

        return [release for release in mod.releases
                if release.version in spec and
                release.game_version == game_ver]

    def resolve_local_requirement(self, req):
        spec = req.specifier
        game_ver = self.config.game_version_major

        return [info for info in self.get_installed_mods(req.name)
                if info.version in spec and
                info.factorio_version == game_ver]

    def is_mod_enabled(self, name):
        mod = self.get_mod_json(name)
        if mod:
            return mod.enabled != 'false'
        else:
            return True  # by default, new mods are automatically enabled

    def set_mod_enabled(self, name, enabled=True):
        mod = self.get_mod_json(name)
        if not mod:
            mod = {'enabled': '', 'name': name}
            self.mods_json.mods.append(mod)

        if enabled != mod.enabled:
            mod.enabled = 'true' if enabled else 'false'
            self.mods_json.save()
            return True
        else:
            return False

    def require_login(self):
        import getpass
        import sys
        player_data = self.config.player_data
        username = player_data.get('service-username')
        token = player_data.get('service-token')

        if not (username and token):
            print('You need a Factorio account to download mods.')
            print('Please provide your username and password to authenticate '
                  'yourself.')
            print('Your username and token (NOT your password) will be stored '
                  'so that you only have to enter it once')
            print('This uses the exact same method used by Factorio itself')
            print()
            while True:
                if username:
                    print('Username [%s]:' % username, end=' ', flush=True)
                else:
                    print('Username:', end=' ', flush=True)

                input_username = sys.stdin.readline().strip()

                if input_username:
                    username = input_username
                elif not username:
                    continue

                password = getpass.getpass('Password (not shown):')
                if not password:
                    continue

                try:
                    token = self.api.login(username, password)
                except AuthError as ex:
                    print('Authentication error: %s.' % ex)
                except Exception as ex:
                    print('Error: %s.' % ex)
                else:
                    print('Logged in successfully.')
                    break
                print()
            player_data['service-token'] = token
            player_data['service-username'] = username
            player_data.save()
        return player_data

    def install_mod(self, release, enable=None, unpack=None):
        file_name = release.file_name
        mod_name = release.info_json.name

        assert '/' not in file_name
        assert '\\' not in file_name
        assert file_name.endswith('.zip')

        try:
            installed_mod = next(self.get_mods(mod_name))
            if unpack is None:
                unpack = not installed_mod.packed
        except StopIteration:
            installed_mod = None

        player_data = self.require_login()
        url = urljoin(self.api.base_url, release.download_url)

        print('Downloading: %s...' % url)

        req = requests.get(
            url,
            params={
                'username': player_data['service-username'],
                'token': player_data['service-token']
            }
        )
        data = req.content

        if len(data) != release.file_size:
            raise Exception(
                'Downloaded file has incorrect size (%d), expected %d.' % (
                    len(data), release.file_size
                )
            )

        file_path = os.path.join(self.config.mods_path, file_name)

        with open(file_path, 'wb') as f:
            f.write(data)

        mod = ZippedMod(self, file_path)

        if installed_mod and (installed_mod.basename != mod.basename or
                              not installed_mod.packed):
            installed_mod.remove()

        if enable is not None:
            mod.enabled = enable

        if unpack:
            mod.unpack()

    def uninstall_mods(self, name, version=None):
        mods_to_remove = self.get_mods(name=name, version=version)

        for mod in mods_to_remove:
            mod.remove()
