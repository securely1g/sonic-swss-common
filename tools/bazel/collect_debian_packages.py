"""Retain the real packages and their source-metadata validation receipts."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--dependency-deb', type=Path, action='append', default=[])
    args = parser.parse_args()
    source = Path('bazel-bin/debian')
    args.destination.mkdir(parents=True, exist_ok=True)
    packages = list(source.glob('*.deb'))
    if len(packages) != 3:
        raise ValueError('Expected exactly three Common Debian packages')
    for path in packages + args.dependency_deb:
        shutil.copy2(path, args.destination / path.name)
    for path in source.rglob('*'):
        if path.is_file() and (path.suffix in ['.json', '.control', '.symbols'] or path.name in ['conffiles', 'shlibs', 'triggers', 'substvars']):
            destination = args.destination / 'metadata' / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


if __name__ == '__main__':
    main()
