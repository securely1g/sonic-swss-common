"""Emit libyang-Python's CFFI C source for compilation by Bazel."""

import argparse
import runpy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    builder = runpy.run_path(args.build)["BUILDER"]
    builder.emit_c_code(args.output)


if __name__ == "__main__":
    main()
