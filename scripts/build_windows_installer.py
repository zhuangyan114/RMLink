"""Build an allowlisted per-user installer; no firmware or kernel drivers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from rmlink_connector import __version__


def compiler_path(explicit):
    candidates = [explicit, shutil.which('ISCC.exe'),
                  ROOT / 'artifacts/tools/inno-setup/compiler/ISCC.exe',
                  Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Inno Setup 6/ISCC.exe']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError('Install Inno Setup 6.7.3 or later, or pass --iscc with its ISCC.exe path')


def verify_signature(path):
    env = dict(os.environ, RMLINK_SIGNATURE_FILE=str(path))
    subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                    '-NoProfile', '-NonInteractive', '-Command',
                    '$signature = Get-AuthenticodeSignature -FilePath $env:RMLINK_SIGNATURE_FILE; '
                    'if ($signature.Status -ne "Valid") { throw "A trusted Authenticode signature is required" }'],
                   env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iscc')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/packages')
    parser.add_argument('--allow-uncommitted', action='store_true', help='Always mark this build as a preview')
    parser.add_argument('--sign-command', help='Inno SignTool command; $f is the quoted file to sign')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('The installer must be built and verified on Windows')
    iscc = compiler_path(args.iscc)
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    clean = not subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain']).strip()
    if not clean and not args.allow_uncommitted:
        parser.error('Review and commit changes before release, or use --allow-uncommitted for a preview')
    if args.sign_command and '$f' not in args.sign_command:
        parser.error('--sign-command must contain the Inno Setup $f placeholder')
    suffix = ('' if args.sign_command else '-unsigned') + ('-preview' if args.allow_uncommitted else '')
    name = f'RMLink-{__version__}-windows-x64-setup{suffix}'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    installer = output / (name + '.exe')
    if installer.exists():
        parser.error('Output exists; preserve the previous installer and choose a new output directory')
    build = ROOT / 'artifacts/build/installer'
    build.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='payload-', dir=build) as temporary:
        workspace = Path(temporary)
        payload = workspace / 'payload'
        payload.mkdir()
        sources = {}

        def add(destination, source):
            target = payload / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            sources[destination] = str(source.relative_to(ROOT))

        dist = ROOT / 'artifacts/build/connector/dist'
        for file in ('rmlink.exe', 'RMLinkConnector.exe', 'Hotspot.ps1'):
            add('bin/' + file, dist / file)
        version = subprocess.check_output([str(payload / 'bin/rmlink.exe'), '--version'], text=True).strip()
        if version != 'RMLink ' + __version__:
            raise RuntimeError('Client is stale; rebuild the connector first')
        for file in ('Manage-Installation.ps1', 'Register-Command.ps1', 'Start-Service.ps1'):
            add(file, ROOT / 'host/distribution' / file)
        for file in ('terminal-client.md', 'current-direction.md', 'shared-target-package.md', 'windows-installer.md'):
            add('docs/' + file, ROOT / 'docs' / file)
        add('README.txt', ROOT / 'docs/windows-installer.md')
        for file in ('RMLink-LICENSE.txt', 'Python-LICENSE.txt', 'PyInstaller-COPYING.txt', 'Orbit-FlashAlgorithm-LICENSE.txt'):
            add('licenses/' + file, dist / file)
        compiler_license = Path(iscc).parent / 'License.txt'
        target = payload / 'licenses/Inno-Setup-LICENSE.txt'
        shutil.copyfile(compiler_license, target)
        sources['licenses/Inno-Setup-LICENSE.txt'] = 'Inno Setup compiler License.txt'
        # Include every bundled algorithm's license, not only the original F103.
        for file in sorted((ROOT / 'artifacts/build/target-pack').glob('*-LICENSE.txt')):
            add('licenses/algorithms/' + file.name, file)
        if args.sign_command:
            # The installed applications must carry the same trusted signature as Setup.
            for file in ('rmlink.exe', 'RMLinkConnector.exe'):
                subprocess.run([iscc, '/Q', '/S' + 'publisher=' + args.sign_command,
                                '/DSignFile=' + str(payload / 'bin' / file),
                                '/O' + str(workspace / 'signing'),
                                str(ROOT / 'host/distribution/Sign-Payload.iss')], check=True)
                verify_signature(payload / 'bin' / file)
        manifest = dict(version=__version__, source_commit=commit, source_clean=clean,
                        preview=args.allow_uncommitted, kernel_driver_included=False,
                        signed=bool(args.sign_command), files=[])
        for relative, source in sorted(sources.items()):
            data = (payload / relative).read_bytes()
            manifest['files'].append(dict(path=relative, source=source, size=len(data),
                                          sha256=hashlib.sha256(data).hexdigest()))
        (payload / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        command = [iscc, '/Q', '/O' + str(output), '/F' + name,
                   '/DPayloadDirectory=' + str(payload), '/DAppVersion=' + __version__]
        if args.sign_command:
            command += ['/DPublisherSignCommand=1', '/Spublisher=' + args.sign_command]
        command.append(str(ROOT / 'host/distribution/RMLink.iss'))
        subprocess.run(command, check=True)
        if args.sign_command:
            verify_signature(installer)
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    installer.with_suffix('.sha256').write_text(digest + '  ' + installer.name + '\n', encoding='ascii')
    installer.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(installer=str(installer), sha256=digest, signed=bool(args.sign_command),
                          source_clean=clean, preview=args.allow_uncommitted)))


if __name__ == '__main__':
    main()
