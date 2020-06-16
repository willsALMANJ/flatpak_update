# flatpak_update.py

This script looks up the latest versions of a flatpak's dependencies and
generates a new manifest with the updated versions.

The script was written before I discovered the existence of [Flatpak External
Data Checker](https://github.com/flathub/flatpak-external-data-checker) which
does basically the same thing. Use that instead :).

For own curiosity, and perhaps others', here are the main differences between this script and flatpak-external-data-checker (fedc):

+ fedc checks that current links still work and give the same checksums. This script only checks for new versions.
So this script is faster when there are no updates because it does not download and checksum versions that did not change.
+ This script downloads archives to a cache directory only if they don't already exist. 
For real operation, you would not want to use a cache so that you are sure to get the current data.
However, when debugging a problem with config file formatting, it is very convenient to use the cache to skip downloading a few large archive files over and over again.
Writing the files to disk also avoids the need to hold the full file contents in memory like fedc does.
+ This script uses asyncio and httpx to perform network operations concurrently.
fedc runs its network requests sequentially.
+ This script uses Jinja2 templating to update files which provides a little more flexibility in inserting the version information at the expense of needing to maintain the template files and the rendered files in the repo.
If maintaining this script, I would try to push the config file data into the manifest like fedc does.
+ fedc inserts new versions into the appdata XML file. This script doesn't but it allows the version to be set with Jinja2.
You only end up with the latest version in the XML file rather than a listing of all versions, but that is good enough.
+ This script checks the run time version as well.
This feature was replicated in https://github.com/willsALMANJ/flatpak_check_runtime.
+ This script's `scrape` checker is similar to the html checker in fedc.
The other checkers are different between the two.
+ This script uses three widely used Python libraries as its only dependencies.
It can be easily set up with `python3 -m venv .env; . .env/bin/activate; python3 -m pip -r requirements.txt`.
fedc only documents a container-based workflow.
+ fedc works with git and GitHub directly.
This script just modifies the files on the filesystem and leaves the git and GitHub operations to another tool (see `github_workflow.yml` in `example/`).
+ fedc has a good unit tests and a number of contributors.
This script has no scripts and a single author.

## How it works

```
$ python flatpak_update.py --help
usage: flatpak_update.py [-h] --config CONFIG --manifest MANIFEST [--template-dir TEMPLATE_DIR]

optional arguments:
  -h, --help            show this help message and exit
  --config CONFIG, -c CONFIG
                        Configuration file
  --manifest MANIFEST, -m MANIFEST
                        Current flatpak manifest
  --template-dir TEMPLATE_DIR, -t TEMPLATE_DIR
                        Directory with .j2 files to render
```

flatpak_update.py loads a yaml configuration file (`CONFIG`) which parametrizes how to determine the latest versions of dependencies.
It also loads the current flatpak manifest file (`MANIFEST`) to compare versions and decide which versions are new.
It then populates the versions (`_version`), checksums (`_sha256`), source urls (`_source_url`), and version dates (`_version_date`) into variables which are prefixed with the `name` entries in `CONFIG` and then renders any Jinja2 templates in `TEMPLATE_DIR` using these variables.

The `CONFIG` file looks roughly like this:

```
runtime:
  name: runtime
  get_version:
    type: github_branches
    project: org/repo
    regex: v([\d\.]+)

modules:
  - name: app
    get_version:
      type: scrape
      regex: app-([\d\.]+)\.tar\.gz
      url: https://app.com/downloads
    source_url: https://app.com/downloads/app-{version}.tar.gz

  - ...
```

`modules` is a list of entries with `name`, `get_version`, and `source_url` sections.
The `get_version` section determines the way to check for new versions.
The different types of checkers (specified as the `type` entry of `get_version`) are:

* **scrape**: download the page at `url` and search for a regex with single group that captures the version string. All matches are found and the one with the largest semantic version is taken as the latest version.

* **github_releases**: use the GitHub API to search the names of all releases of the repo at `project` on GitHub (`project` is for example `flathub/flatpak-external-data-checker`). The name with the largest semantic version is taken as the latest version. If a `tags` entry is present and set to `True`, search the git tags instead of the GitHub releases. A `substitutions` entry can also be specified which is a list of lists containing two strings. The first string is replaced with the second before converting the name into a semantic version (e.g. `["v", ""]` converts `v0.0.1` to `0.0.1`).

* **github_branches**: search all the git branches for the repo at `project` on GitHub (see description in `github_releases` above) for matches to a `regex` entry which contains a single group that captures the version number inside of the branch.

`source_url` is a Python format string which will be called with `.format(version=version)` to substitute in the new version for downloading and checksumming the new source code.

An example configuration is provided in example/
