#!/usr/bin/env python
'''Freeze container requirements for use with a final container build.'''

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys


def run(command: str | list[str], capture_output: bool = False, container_id: None | str = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, text=True, capture_output=capture_output)
    except subprocess.CalledProcessError as err:
        if container_id:
            if capture_output:
                print(f'ERROR: {err.stderr}, {err.stdout}')

            print(f'Stopping container {container_id}')
            subprocess.run(['docker', 'stop', container_id])

        sys.exit(err.returncode)


def main() -> None:
    '''Main entry point.'''
    parser = argparse.ArgumentParser()
    parser.add_argument('--container-runtime', default='docker')
    parser.add_argument('container', nargs='?', default='koku-test-container-freezer')
    parser.add_argument('--no-cache', action='store_true')

    args = parser.parse_args()
    container_runtime = args.container_runtime
    container = args.container
    no_cache = args.no_cache

    build_command = [container_runtime, 'build', '--tag', container, '--file', 'Freezer', '.']
    if no_cache:
        build_command.insert(2, '--no-cache')

    print('Building a container to freeze the requirements.')
    run(build_command)
    container_id = run([container_runtime, 'run', '--rm', '--tty', '--detach', container], capture_output=True).stdout.rstrip()

    print('Freezing requirements')
    freezer_venv = '/opt/venvs/freezer'
    command = [
        container_runtime, 'exec', container_id,
        'python', '-m', 'venv', freezer_venv
    ]
    run(command, container_id=container_id)

    command = [
        container_runtime, 'exec', container_id,
        f'{freezer_venv}/bin/python', '-m', 'pip', 'install', '--upgrade', '--disable-pip-version-check',
        '--requirement', '/usr/share/container-setup/requirements/requirements.in',
        '--constraint', '/usr/share/container-setup/requirements/constraints.txt',
    ]
    run(command, container_id=container_id)

    command = [
        container_runtime, 'exec', container_id,
        f'{freezer_venv}/bin/python', '-m', 'pip', 'freeze', '-qqq', '--disable-pip-version-check',
    ]
    freeze = run(command, capture_output=True, container_id=container_id).stdout

    freeze_file = pathlib.Path('requirements/requirements.txt')
    freeze_file.write_text(freeze)

    run([container_runtime, 'stop', container_id])

    print(f'Freezing complete. Requirements in {freeze_file} updated.')


if __name__ == '__main__':
    main()
