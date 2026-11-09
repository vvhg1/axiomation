import re
import tomllib
from pathlib import Path

import axiomation

ROOT = Path(__file__).resolve().parents[1]
#: Where PyPI reads the light banner: GitHub's raw file at the release's tag.
BANNER = "https://raw.githubusercontent.com/vvhg1/axiomation/v{}/docs/assets/banner-light.svg"


def test_the_package_imports_with_a_version():
    assert axiomation.__version__


def test_the_readme_shows_the_banner_of_github_s_theme():
    """On GitHub, each banner marked for its theme by GitHub's fragments."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'src="docs/assets/banner-light.svg#gh-light-mode-only"' in readme
    assert 'src="docs/assets/banner-dark.svg#gh-dark-mode-only"' in readme


def test_pypi_is_given_the_light_banner_of_this_release():
    """The description PyPI receives is the README with its banner block
    rewritten at build time (`hatch-fancy-pypi-readme`'s substitutions,
    `re.sub` each): the light banner alone, at this release's tag, no
    fragment and no relative path PyPI cannot show."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    hook = project["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]
    assert "readme" in project["project"]["dynamic"]
    assert [f["path"] for f in hook["fragments"]] == ["README.md"]
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for s in hook["substitutions"]:
        text = re.sub(s["pattern"], s["replacement"], text)
    assert BANNER.format(axiomation.__version__) in text
    assert "#gh-" not in text and "docs/assets/" not in text.replace(
        BANNER.format(axiomation.__version__), ""
    )
