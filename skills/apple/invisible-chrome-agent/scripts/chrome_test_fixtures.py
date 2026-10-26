"""Short, private Unix-socket fixtures; never change tempfile's global root."""
import os
from pathlib import Path
import tempfile
import sys


def fresh_profile_arguments(directory, profile):
    """Select test-only flags once, before launching a disposable profile.

    Keep the TemporaryDirectory alive through all wakes and child cleanup.
    This is not a production launch option or a real-Keychain acceptance test.
    """
    if not isinstance(directory, tempfile.TemporaryDirectory):
        raise ValueError('a live disposable TemporaryDirectory is required')
    root = Path(directory.name)
    profile = Path(profile)
    if (not root.is_dir() or root.is_symlink() or profile.parent != root
            or profile.is_symlink() or profile.exists()):
        raise ValueError('refusing existing or non-isolated fixture profile')
    args = ['--user-data-dir=' + str(profile)]
    if sys.platform == 'darwin':
        args.append('--use-mock-keychain')
    return args


def chrome_environment(root):
    """Chrome's launcher/crash caches are outside --user-data-dir by default."""
    root = Path(root)
    return dict(HOME=str(root), XDG_DATA_HOME=str(root / 'data'), XDG_CONFIG_HOME=str(root / 'config'),
                XDG_CACHE_HOME=str(root / 'cache'), CHROME_CONFIG_HOME=str(root / 'config'))


def socket_directory():
    # macOS's default /var/folders tempfile root is too long for a capability
    # socket. Honor an explicitly selected managed scratch root, not /tmp.
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    configured = os.environ.get('TMPDIR')
    system_roots = ('/tmp', '/private/tmp', '/var/folders', '/private/var/folders')
    if configured and not any(configured == p or configured.startswith(p + '/') for p in system_roots):
        root = Path(configured)
    else:
        root = home / 'cache' / 'scratch'
    root.mkdir(parents=True, exist_ok=True)
    directory = tempfile.TemporaryDirectory(prefix='', dir=root)
    path = Path(directory.name) / ('bu-lease-' + 'x' * 43 + '.sock')
    if len(os.fsencode(path)) > 103:
        directory.cleanup()
        raise ValueError('managed scratch root too long for portable Unix sockets; select a shorter TMPDIR')
    return directory
