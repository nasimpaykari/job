"""Version-aware installer. This file can itself run on Python 3.8+."""
import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys

MINIMUM = (3, 10)


def probe(executable):
    """Return a supported interpreter's absolute path, or None."""
    try:
        result = subprocess.run(
            list(executable) + ['-c', 'import sys; print(sys.executable); print("%d.%d" % sys.version_info[:2])'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
            timeout=15,
        )
        lines = result.stdout.strip().splitlines()
        if result.returncode != 0 or len(lines) < 2:
            return None
        version = tuple(int(n) for n in lines[-1].split('.'))
        return lines[-2] if version >= MINIMUM else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def find_python():
    candidates = []
    if os.name == 'nt' and shutil.which('py'):
        # Prefer the tested Python 3.12 family, then other supported versions.
        candidates.extend([['py', '-'+v] for v in ('3.12', '3.13', '3.14', '3.11', '3.10')])
        candidates.append(['py', '-3'])
    candidates.append([sys.executable])
    for name in ('python3.12', 'python3.13', 'python3.14', 'python3.11', 'python3.10', 'python3', 'python'):
        found = shutil.which(name)
        if found:
            candidates.append([found])
    for command in candidates:
        found = probe(command)
        if found:
            return found
    return None


def run_checked(command, cwd):
    subprocess.run(command, cwd=str(cwd), check=True)


def ensure_environment(root, interpreter):
    root = Path(root)
    folder = root / '.venv'
    executable = folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if folder.exists() and not probe([str(executable)]):
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = root / ('.venv-incompatible-' + stamp)
        folder.rename(backup)
        print('Preserved the incompatible environment as ' + backup.name, flush=True)
    if not folder.exists():
        print('Creating an environment with ' + interpreter, flush=True)
        run_checked([interpreter, '-m', 'venv', str(folder)], root)
    if not probe([str(executable)]):
        raise RuntimeError('The virtual environment does not contain Python 3.10 or newer.')
    return executable


def main():
    root = Path(__file__).resolve().parent
    interpreter = find_python()
    if not interpreter:
        print('\nPython 3.10 or newer is required. This installer did not find one.')
        print('Install Python 3.12 or 3.13 (64-bit) from https://www.python.org/downloads/windows/')
        print('Include the Python launcher, then run start_windows.bat again.')
        print('Your existing data folder and old environment have not been changed.')
        return 1
    try:
        executable = ensure_environment(root, interpreter)
        run_checked([str(executable), '--version'], root)
        print('\nUpdating pip inside the app environment...', flush=True)
        run_checked([str(executable), '-m', 'ensurepip', '--upgrade'], root)
        run_checked([str(executable), '-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel'], root)
        print('\nInstalling Job Coach dependencies...', flush=True)
        run_checked([str(executable), '-m', 'pip', 'install', '-r', 'requirements.txt'], root)
        run_checked([str(executable), 'main.py'], root)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print('\nSetup or startup could not finish: ' + str(error))
        print('See the specific error above. Your job data has not been deleted.')
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
