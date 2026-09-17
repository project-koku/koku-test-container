#!/usr/bin/env python

import io
import json
import platform
import tarfile
import urllib.parse
import urllib.request

from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def get_architecture() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in {"arm64", "aarch64"} else "amd64"


def install_oc(output_dir: Path = Path("/usr/local/bin")) -> str:
    version = "4.16"
    # The bare linux tarball is amd64; arm64 has a dedicated -arm64 suffix.
    suffix = "-arm64" if get_architecture() == "arm64" else ""
    url = f"https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable-{version}/openshift-client-linux{suffix}.tar.gz"
    with urllib.request.urlopen(url) as resp:
        b_tar_data = io.BytesIO(resp.read())

    members_to_extract = ["oc"]
    with tarfile.open(fileobj=b_tar_data) as archive:
        for member in members_to_extract:
            archive.extract(member, path=str(output_dir))

    return f"Installed {', '.join(members_to_extract)} to {output_dir}"


def install_mc(output_dir: Path = Path("/usr/local/bin")) -> str:
    system = platform.system().lower()
    architecture = get_architecture()
    # The dl.min.io CDN path was decommissioned (HTTP 410 Gone); fetch from GitHub releases.
    # Standalone binaries are named mc.<os>-<arch>.<RELEASE-tag>; resolve the current tag first.
    api_url = "https://api.github.com/repos/minio/mc/releases/latest"
    with urllib.request.urlopen(api_url) as resp:
        release = json.load(resp)

    asset_name = f"mc.{system}-{architecture}.{release['tag_name']}"
    url = f"https://github.com/minio/mc/releases/download/{release['tag_name']}/{asset_name}"
    mc_path = output_dir / "mc"
    with urllib.request.urlopen(url) as resp:
        mc_path.write_bytes(resp.read())

    mc_path.chmod(0o0755)

    return f"Installed {mc_path} ({release['tag_name']})"


def main() -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(install_oc), executor.submit(install_mc)}
        for future in as_completed(futures):
            print(future.result())


if __name__ == "__main__":
    main()
