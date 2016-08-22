from pkg_resources import parse_version

from fac.commands import Command, Arg
from fac.api import ModNotFoundError
from fac.utils import parse_requirement


class InstallCommand(Command):
    '''
    Install (or update) mods

    This will install mods matching the given requirements using this format:
        name
        name==version
        name>=version
        name<version
        ...

    If the version is not specified, the latest version will be selected.

    Outdated versions will be replaced.
    '''
    name = 'install'

    arguments = [
        Arg('requirement', nargs='+',
            help='requirement ("name", "name>=1.0", "name==1.2", ...)'),

        Arg('--held', action='store_true',
            help='allow updating held mods'),

        Arg('--reinstall', action='store_true',
            help='allow reinstalling mods'),

        Arg('--downgrade', action='store_true',
            help='allow downgrading mods'),

        Arg('--unpack', action='store_true', default=None,
            help='unpack mods zip files'),

        # Arg('-o', '--install-optdeps', action='store_true',
        #    help='install all optional dependencies'),

        Arg('-d', '--no-deps', action='store_true',
            help='do not install any dependencies'),
    ]

    def run(self, args):
        # TODO: handle optional dependencies
        to_install = []

        for req in args.requirement:
            req = parse_requirement(req)

            try:
                releases = self.manager.resolve_remote_requirement(req)
            except ModNotFoundError:
                print('%s: this mod does not exist.' % req.name)
                continue

            if not args.held and req.name in self.config.hold:
                print('%s is held. '
                      'Use --held to install it anyway.' % (req.name))
                continue

            if not releases:
                print('No match found for %s' % (req,))
                continue

            local_mod = self.manager.get_mod(req.name)

            for release in releases:
                if local_mod:
                    local_ver = parse_version(local_mod.version)
                    release_ver = parse_version(release.version)

                    if not args.reinstall and release_ver == local_ver:
                        print('%s==%s is already installed. '
                              'Use --reinstall to reinstall it.' % (
                                  local_mod.name, local_ver))
                        break

                    elif not args.downgrade and release_ver < local_ver:
                        print(
                            '%s is already installed in a more recent version.'
                            ' Use --downgrade to downgrade it.' % (
                                local_mod.name
                            )
                        )
                        break

                if args.no_deps:
                    deps = []
                else:
                    deps = release.info_json.dependencies
                deps_to_install = []
                deps_ok = True

                for dep in deps:
                    depreq = parse_requirement(dep)

                    if depreq.name.startswith('?'):
                        continue  # ignore optional dependency

                    if depreq.name == 'base':
                        if self.config.game_version in depreq.specifier:
                            continue
                        else:
                            print('%s is incompatible with game version %s' % (
                                depreq, self.config.game_version,
                            ))
                            deps_ok = False
                            break

                    if self.manager.resolve_local_requirement(depreq):
                        continue
                    try:
                        rels = self.manager.resolve_remote_requirement(depreq)
                    except ModNotFoundError:
                        print('Dependency not found: %s' % depreq.name)
                        deps_ok = False
                        break

                    if not rels:
                        print('Dependency can not be met: %s' % depreq)
                        deps_ok = False
                        break

                    # FIXME: we only try the first release here
                    deprel = rels[0]
                    print('Adding dependency: %s %s' % (
                        depreq.name, deprel.version
                    ))
                    deps_to_install.append(deprel)

                if deps_ok:
                    to_install += deps_to_install
                    to_install.append(release)
                    break

        for release in to_install:
            print('Installing: %s %s...' % (
                release.info_json.name, release.version
            ))

            self.manager.install_mod(release, unpack=args.unpack)
