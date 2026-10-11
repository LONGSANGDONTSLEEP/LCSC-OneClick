"""Make repository-relative links in release notes usable on GitHub Releases."""
import argparse
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'Travelerrrrrr/LCSC3D'
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}


def prepare(source: Path, tag: str) -> str:
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Expected a stable vX.Y.Z tag')
    source = source.resolve()
    if not source.is_relative_to(ROOT):
        raise ValueError('Release notes must be inside the repository')

    def public_url(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or value.startswith('#'):
            return value
        target = (source.parent / unquote(parsed.path)).resolve()
        if not target.is_relative_to(ROOT) or not target.is_file():
            raise ValueError(f'Missing or external release-note reference: {value}')
        path = quote(target.relative_to(ROOT).as_posix(), safe='/')
        if target.suffix.lower() in IMAGE_SUFFIXES:
            url = f'https://raw.githubusercontent.com/{REPOSITORY}/{tag}/{path}'
        else:
            url = f'https://github.com/{REPOSITORY}/blob/{tag}/{path}'
        if parsed.fragment:
            url += '#' + quote(unquote(parsed.fragment), safe='-')
        return url

    content = source.read_text(encoding='utf-8')
    content = re.sub(r'\]\(([^\s)]+)\)', lambda m: '](' + public_url(m[1]) + ')', content)
    return re.sub(r'((?:src|href)=")([^"]+)(")',
                  lambda m: m[1] + public_url(m[2]) + m[3], content)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(prepare(args.source, args.tag), encoding='utf-8', newline='\n')
    print(f'Prepared public release notes: {args.output}')
