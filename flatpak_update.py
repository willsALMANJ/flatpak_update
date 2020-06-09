"Update a Flatpak repository for new versions of components"
import argparse
import asyncio
import datetime
from functools import total_ordering
import hashlib
from itertools import zip_longest
import json
from pathlib import Path
import re

import httpx
import jinja2
import yaml


GITHUB_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@total_ordering
class Version:
    """Class embodying a software version

    Assumes that version is purely composed of integers and .'s -- no alpha,
    beta, prelease, etc. or other strings allowed.
    """
    def __init__(self, version):
        if isinstance(version, str):
            version_tuple = tuple(version.split("."))
        elif isinstance(version, tuple):
            version_tuple = version
        else:
            raise ValueError(f"Invalid version: {version}")

        self.version_tuple = tuple(int(x) for x in version_tuple)
        self.date = None

    def __iter__(self):
        for part in self.version_tuple:
            yield part

    def __str__(self):
        return ".".join(str(p) for p in self)

    def __repr__(self):
        return f"{type(self).__name__}({str(self)})"

    def __len__(self):
        return len(self.version_tuple)

    def __hash__(self):
        return hash(tuple(self))

    def __getitem__(self, key):
        if len(self) > key:
            return self.version_tuple[key]
        return 0

    def __eq__(self, other):
        return str(self) == str(other)

    def __gt__(self, other):
        for self_i, other_i in zip_longest(self, other, fillvalue=0):
            if self_i == other_i:
                continue
            return self_i > other_i

        return False


async def get_version_scrape(spec):
    "Scrape raw HTML for version string regex to find latest version"
    async with httpx.AsyncClient() as client:
        response = await client.get(spec["url"])
    matches = re.findall(spec["regex"], response.text)
    version = max(Version(m) for m in matches)

    return version


async def get_version_github_branches(spec):
    "Get latest version for project that uses separate git tag for each version"
    base_url = f"https://api.github.com/repos/{spec['project']}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base_url}/branches", headers=headers)
    data = response.json()
    versions = [
        Version(m.group(1))
        for b in data
        if (m := re.match(spec["regex"], b["name"]))
    ]
    version = max(versions)

    return version


async def get_version_github_releases(spec):
    "Find the latest version on GitHub releases / tags page"
    base_url = f"https://api.github.com/repos/{spec['project']}"
    headers = {"Accept": "application/vnd.github.v3+json"}

    if spec.get("tags"):
        endpt = "tags"
    else:
        endpt = "releases"
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base_url}/{endpt}", headers=headers)
    data = response.json()
    versions = []
    for item in data:
        version_str = item["name"]
        for sub in spec.get("substitutions", []):
            version_str = version_str.replace(sub[0], sub[1])

        try:
            version = Version(version_str)
        except ValueError:
            continue
        versions.append((version, item))
    version, metadata = max(versions)

    if spec.get("set_date"):
        if endpt == "releases":
            version_dt = datetime.datetime.strptime(
                metadata["published_at"], GITHUB_DATE_FORMAT
            )
        elif endpt == "tags":
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    metadata["commit"]["url"], headers=headers
                )
            data = response.json()
            version_dt = datetime.datetime.strptime(
                data["commit"]["committer"]["date"], GITHUB_DATE_FORMAT
            )
        version.date = version_dt.strftime("%Y-%m-%d")

    return version


async def get_latest_version(spec):
    "Get latest version of a single component"
    if spec["type"] == "scrape":
        version = get_version_scrape(spec)
    elif spec["type"] == "github_branches":
        version = get_version_github_branches(spec)
    elif spec["type"] == "github_releases":
        version = get_version_github_releases(spec)
    else:
        raise ValueError(f"Bad spec type: {spec['type']}")

    return version


async def get_latest_versions(specs):
    "Look up latest versions of components in config file"
    versions = await asyncio.gather(
        *(get_latest_version(s["get_version"]) for s in specs)
    )
    return {s["name"]: v for s, v in zip(specs, versions)}


def load_manifest(path):
    "Load json or yaml file"
    with path.open("r") as file_:
        if path.suffix == ".json":
            manifest = json.load(file_)
        else:
            manifest = yaml.load(file_, Loader=yaml.SafeLoader)

    return manifest


def get_current_versions(manifest):
    "Parse versions from current Flatpak manifest"
    versions = {"runtime": Version(manifest["runtime-version"])}
    for module in manifest["modules"]:
        match = re.search(r"-([0-9\.]+)\.tar\.gz$", module["sources"][0]["url"])
        versions[module["name"]] = Version(match.group(1))

    return versions


async def get_sha256(download_dir: Path, url):
    "Get sha256 sum for url"
    output_path = download_dir / Path(url).name
    if not output_path.exists():
        client = httpx.AsyncClient()
        with output_path.open("wb") as file_:
            async with client.stream("GET", url) as response:
                async for chunk in response.aiter_raw():
                    file_.write(chunk)

    with output_path.open("rb") as file_:
        sha256 = hashlib.sha256()
        while True:
            data = file_.read(2 ** 16)
            if not data:
                break
            sha256.update(data)

    return sha256.hexdigest()


async def get_sha256_set(named_urls):
    """Get sha256 sums for name:url pairs

    Returns dictionary with {name}_sha256 keys for easy merging into j2
    variables dict
    """
    cache_dir = Path.cwd() / ".cache"
    cache_dir.mkdir(exist_ok=True)

    sums = await asyncio.gather(
        *(get_sha256(cache_dir, v) for v in named_urls.values())
    )

    sha256_vars = [f"{n}_sha256" for n in named_urls]

    return dict(zip(sha256_vars, sums))


def get_template_vars(config, current_versions, new_versions, manifest):
    "Build up variables for jinja2 templates from version data"
    env = {}
    remote_sha256 = {}
    env["runtime_version"] = new_versions["runtime"]
    for spec in config["modules"]:
        name = spec["name"]
        env[f"{name}_version"] = new_versions[name]
        env[f"{name}_source_url"] = spec["source_url"].format(
            version=new_versions[name]
        )
        env[f"{name}_version_date"] = new_versions[name].date
        if new_versions[name] > current_versions[name]:
            remote_sha256[name] = env[f"{name}_source_url"]
        else:
            for mod in manifest["modules"]:
                if mod["name"] == name:
                    env[f"{name}_sha256"] = mod["sources"][0]["sha256"]

    new_sha256 = asyncio.run(get_sha256_set(remote_sha256))
    env.update(**new_sha256)

    return env


def render_templates(template_dir, env):
    "Render a .j2 templates using collected version information"
    for path in template_dir.glob("*.j2"):
        with path.open("r") as file_:
            template = jinja2.Template(file_.read())
        with path.with_name(path.stem).open("w") as file_:
            file_.write(template.render(**env))


def parse_args():
    "Parse command line arguments"
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", "-c", required=True, help="Configuration file")
    parser.add_argument(
        "--manifest", "-m", required=True, help="Current flatpak manifest"
    )
    parser.add_argument(
        "--template-dir", "-t", help="Directory with .j2 files to render"
    )
    return parser.parse_args()


def main():
    "Main logic"
    args = parse_args()
    with open(args.config) as file_:
        config = yaml.load(file_, Loader=yaml.SafeLoader)
    new_versions = asyncio.run(
        get_latest_versions([config["runtime"]] + config["modules"])
    )
    manifest = load_manifest(Path(args.manifest))
    current_versions = get_current_versions(manifest)

    env = get_template_vars(config, current_versions, new_versions, manifest)

    render_templates(Path(args.template_dir), env)


if __name__ == "__main__":
    main()
