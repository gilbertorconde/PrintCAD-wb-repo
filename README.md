# printCAD workbench registry

The list of workbench packages printCAD offers in Preferences › Workbench
packages › Browse. Each package lives in its author's own GitHub
repository; this registry lists them, checks them, and publishes one index
the app reads:

    https://gilbertorconde.github.io/PrintCAD-wb-repo/index.json

Installing from a file or from any GitHub address still works in printCAD
without the registry. Being listed only makes a package easy to find.

## Adding a workbench

1. Publish the package: a GitHub release with the `.pcbench` file attached
   ([how to build one](https://github.com/gilbertorconde/printCAD/blob/master/docs/PLUGINS.md),
   or start from [PrintCAD-example-wb](https://github.com/gilbertorconde/PrintCAD-example-wb),
   which releases on tags).
2. Fork this repository and add `packages/<id>.toml`, named after the
   package's id:

   ```toml
   id = "acme.cam"
   name = "CAM"
   description = "Toolpaths for milling, from the solids in a document"
   repository = "acme/printcad-cam"
   maintainers = ["acme-dev"]
   license = "MIT"
   categories = ["printing"]
   ```

3. Open a pull request. The checks run on it; once they pass and the entry
   is reviewed, it is merged, and the package shows in printCAD once the
   index is built again, a few minutes later.

## The entry

| Field | |
| --- | --- |
| `id` | The package's id, as in its `bench.toml`: lowercase, reverse-domain (`acme.cam`). The file is named after it. |
| `name` | Its name as printCAD shows it. |
| `description` | One sentence, at most 200 characters. |
| `repository` | `owner/name` on GitHub. Its latest release is what printCAD installs. |
| `maintainers` | GitHub logins of who may change the entry. |
| `license` | The package's license, an SPDX expression. |
| `categories` | One or more of `modeling`, `sketching`, `parts`, `fasteners`, `printing`, `analysis`, `import-export`, `assembly`, `utilities`. |
| `homepage` | Optional: an `https://` address for its documentation. |
| `removed` | Set by the registry when a package is taken off the list: why. printCAD warns whoever has it installed. |

The version, what the package may reach and its download are not in the
entry: they are read from the latest release every time the index is
built.

## What the checks do

On every pull request:

- every entry reads, has its fields, and no id or repository is listed
  twice;
- each entry added or changed: its repository's latest release has a
  `.pcbench` file, the download matches GitHub's checksum, the archive
  holds `bench.wasm` and a `bench.toml` with the same id, and it targets
  the workbench contract printCAD speaks. The checks' summary lists what
  it asks to reach beyond its own folder;
- an entry is added by one of its maintainers, and changed or removed only
  by one of the maintainers it had.

## Releases and updates

A listed package is trusted by its repository: a new release there reaches
printCAD at the next index (built on every change here and once a day),
with no pull request. printCAD checks the download against the index's
checksum and shows what each release asks to reach before it is installed
or updated.

## A word of care

Workbench packages are written by other people. A package runs sandboxed in
its own folder unless you allow more, but `helper` lets it run programs on
your computer and `network` lets it reach the internet. The registry checks
that a package is what it says it is; it does not audit its code. Install
what you trust, and allow only what a package needs.

## Running the tools

Python 3.11 or newer, nothing else:

```sh
python3 -m unittest discover -s tools          # the tools' own tests
python3 tools/registry.py check                # every entry
python3 tools/registry.py check --online packages/acme.cam.toml
python3 tools/registry.py index --out site/index.json
```

`GITHUB_TOKEN` (for example `GITHUB_TOKEN=$(gh auth token)`) lifts GitHub's
limit on requests.

The index is published by `.github/workflows/publish.yml` to GitHub Pages;
the repository's Settings › Pages takes its source from GitHub Actions.

## License

The registry's tools and entries are under MIT or Apache-2.0, at your
option. Each package keeps its own license.
