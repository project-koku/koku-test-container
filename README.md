# koku-test-container #

This is the container used for running [koku]() integration tests in Konflux. It is used by the `bonfire-tekton` pipeline.

## Building the container ##

This container is built automatically when changes are merged.

To manually build the container for local testing, run `./build.py`.

## Updating requirements ##

Files in the `requirements` directory are used for managing Python requirements.

`requirements.in` contains direct dependencies. `constraints.txt` is for restricting versions of indirect dependencies. These two files are used when generating the `requirements.txt` freeze file.

Run `./freeze.py` to generate updated an requirements file.
