from __future__ import annotations

import argparse
import struct
from pathlib import Path


TRIANGLE_SIZE = 50


def triangle_count(path: Path) -> int:
    with path.open("rb") as handle:
        header = handle.read(84)
    if len(header) != 84:
        raise ValueError(f"STL header is too short: {path}")
    count = struct.unpack_from("<I", header, 80)[0]
    if path.stat().st_size != 84 + count * TRIANGLE_SIZE:
        raise ValueError(f"Only binary STL files are supported: {path}")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Combine module STL files without changing their shared coordinates and "
            "write one triangle-to-module map for the PC robot viewer."
        )
    )
    parser.add_argument("output_stl", type=Path)
    parser.add_argument("output_map", type=Path)
    parser.add_argument("modules", type=Path, nargs="+")
    args = parser.parse_args()

    if len(args.modules) > 65535:
        raise ValueError("Module count exceeds uint16 capacity")

    counts = [triangle_count(path) for path in args.modules]
    total = sum(counts)
    args.output_stl.parent.mkdir(parents=True, exist_ok=True)
    args.output_map.parent.mkdir(parents=True, exist_ok=True)

    header_text = "Haikan PC viewer: " + ", ".join(path.stem for path in args.modules)
    header = header_text.encode("ascii", "replace")[:80].ljust(80, b"\0")
    with args.output_stl.open("wb") as output:
        output.write(header)
        output.write(struct.pack("<I", total))
        for path in args.modules:
            with path.open("rb") as source:
                source.seek(84)
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)

    with args.output_map.open("wb") as output:
        output.write(b"HCP1")
        output.write(struct.pack("<II", total, len(args.modules)))
        for module_id, count in enumerate(counts):
            value = struct.pack("<H", module_id)
            block_size = 32768
            for start in range(0, count, block_size):
                output.write(value * min(block_size, count - start))

    print(f"triangles={total} modules={len(args.modules)}")
    for module_id, (path, count) in enumerate(zip(args.modules, counts)):
        print(f"{module_id}: {path.name} triangles={count}")
    print(args.output_stl)
    print(args.output_map)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
