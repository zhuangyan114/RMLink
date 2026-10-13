"""Per-user state. API bearer token stays local and is never logged."""
import json
import os
from pathlib import Path
import secrets


def default_directory():
    # Shared by ordinary IDE processes and packaged desktop launchers. AppData
    # virtualization can otherwise give those processes different credentials.
    return Path(os.environ.get('USERPROFILE', Path.home())) / '.rmlink'


class State:
    def __init__(self, directory=None, create=True):
        self.directory = Path(directory or default_directory())
        self.directory.mkdir(parents=True, exist_ok=True)
        # Packaged Windows hosts can redirect AppData to another volume. Resolve
        # the created directory before the temp-file rename, so both paths stay
        # on the same physical filesystem (also works outside packaged hosts).
        self.directory = self.directory.resolve()
        self.path = self.directory / 'connector.json'
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        elif create:
            self.data = {'version': 1, 'token': secrets.token_hex(32), 'paired': {}}
            self.save()
        else: raise FileNotFoundError('Start the RMLink connector first')
        if self.data.get('version') != 1 or len(self.data.get('token', '')) != 64:
            raise ValueError('Unsupported local connector state')

    def save(self):
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(self.data, out, indent=2); out.write('\n')
        os.replace(temp, self.path)
