[![Static Badge](https://img.shields.io/badge/Quay.io-container-%23EE0000?style=for-the-badge&link=https%3A%2F%2Fquay.io%2Frepository%2Fproject-koku%2Fkoku-test-container "Container on Quay.io")](https://quay.io/repository/project-koku/koku-test-container)
# koku-test-container #

This is the container used for running [koku] [integration tests] in Konflux.

### Building the container ###

This container is built automatically when changes are pushed or tags are created.

To manually build the container for local testing, run `./build.py`. Add `--push` to push the image after building.

### Updating requirements ###

Files in the `requirements` directory are used for managing Python requirements.

`requirements.in` contains direct dependencies. `constraints.txt` is for restricting versions of indirect dependencies. These two files are used when generating the `requirements.txt` freeze file.

Run `./freeze.py` to generate updated an requirements file.


[koku]: https://github.com/project-koku/koku
[integration tests]: https://github.com/project-koku/koku-ci
