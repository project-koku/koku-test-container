#!/usr/bin/env python

import io
from pathlib import Path
import platform
import tarfile
import urllib.request
import urllib.parse

from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor


def install_oc(output_dir: Path = Path('/usr/local/bin')) -> str:
    version = '4.16'
    url = f'https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable-{version}/openshift-client-linux.tar.gz'
    with urllib.request.urlopen(url) as resp:
        b_tar_data = io.BytesIO(resp.read())

    members_to_extract = ['oc']
    with tarfile.open(fileobj=b_tar_data) as archive:
        for member in members_to_extract:
            archive.extract(member, path=str(output_dir))

    return f'Installed {", ".join(members_to_extract)} to {output_dir}'


def install_mc(output_dir: Path = Path('/usr/local/bin')) -> str:
    system = platform.system().lower()
    architecture = "arm64" if "arm" in platform.machine() else "amd64"
    url = f'https://dl.min.io/client/mc/release/{system}-{architecture}/mc'
    mc_path = output_dir / 'mc'
    with urllib.request.urlopen(url) as resp:
        mc_path.write_bytes(resp.read())

    mc_path.chmod(0o0755)

    return f'Installed {mc_path}'


def main() -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(install_oc), executor.submit(install_mc)}
        for future in as_completed(futures):
            print(future.result())


if __name__ == '__main__':
    main()
