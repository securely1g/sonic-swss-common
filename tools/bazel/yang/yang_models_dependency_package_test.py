"""Verify the model dependency archive installs the prepared Make payload."""

from pathlib import Path, PurePosixPath
import sys
import tarfile


def main():
    archive, models = sys.argv[1:]
    model_root = Path(models)
    install_root = PurePosixPath("usr/local/yang-models")
    expected = {
        str(install_root / path.relative_to(model_root)): path.read_bytes()
        for path in model_root.rglob("*")
        if path.is_file()
    }
    if not expected or any(not name.endswith(".yang") for name in expected):
        raise AssertionError("Prepared model payload must contain only YANG files")

    actual = {}
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            path = PurePosixPath(member.name)
            if member.isdir():
                if path not in install_root.parents and not path.is_relative_to(install_root):
                    raise AssertionError(f"Unexpected packaged directory: {path}")
                continue
            if not member.isfile():
                raise AssertionError(f"Model payload must use regular files: {path}")
            with package.extractfile(member) as source:
                actual[str(path)] = source.read()

    if actual != expected:
        missing = sorted(expected.keys() - actual.keys())
        unexpected = sorted(actual.keys() - expected.keys())
        changed = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
        raise AssertionError(f"Model payload differs: missing={missing}, unexpected={unexpected}, changed={changed}")
    print(f"Verified {len(actual)} models installed under /{install_root}")


if __name__ == "__main__":
    main()
