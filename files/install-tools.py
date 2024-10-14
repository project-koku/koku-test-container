#!/usr/bin/env python

import io
import pathlib
import platform
import tarfile
import urllib.request
import urllib.parse


def install_oc() -> None:
    print('Installing oc')
    url = 'https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz'
    with urllib.request.urlopen(url) as resp:
        b_tar_data = io.BytesIO(resp.read())

    members_to_extract = ['oc', 'kubectl']
    with tarfile.open(fileobj=b_tar_data) as archive:
        for member in members_to_extract:
            archive.extract(member, path='/usr/local/bin')


def install_mc():
    print('Installing mc')
    system = platform.system().lower()
    architecture = "arm64" if "arm" in platform.machine() else "amd64"
    url = f'https://dl.min.io/client/mc/release/{system}-{architecture}/mc'
    mc_path = pathlib.Path('/usr/local/bin/mc')
    with urllib.request.urlopen(url) as resp:
        mc_path.write_bytes(resp.read())

    mc_path.chmod(0o0755)


def main() -> None:
    install_oc()
    install_mc()


if __name__ == '__main__':
    main()
