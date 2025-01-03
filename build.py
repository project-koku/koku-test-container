#!/usr/bin/env python

import argparse
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-runtime", default="docker")
    parser.add_argument("image", nargs="?", default="quay.io/project-koku/koku-test-container")
    parser.add_argument("--version", default="latest")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--file", "-f", default="Containerfile")
    parser.add_argument("--push", "-p", action="store_true")

    args = parser.parse_args()
    container_runtime = args.container_runtime
    tag = f"{args.image}:{args.version}"

    command = [container_runtime, "build", "--tag", tag, "--file", args.file, "."]

    if args.no_cache:
        command.insert(2, "--no-cache")

    subprocess.run(command, check=True)

    if args.push:
        subprocess.run(["docker", "push", tag])


if __name__ == "__main__":
    main()
