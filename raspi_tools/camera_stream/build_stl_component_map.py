from __future__ import annotations

import argparse
import struct
import time
from pathlib import Path

import numpy as np


STL_TRIANGLE_DTYPE = np.dtype(
    [
        ("normal", "<f4", (3,)),
        ("vertices", "<f4", (3, 3)),
        ("attribute", "<u2"),
    ]
)


def read_triangle_count(path: Path) -> int:
    with path.open("rb") as handle:
        header = handle.read(84)
    if len(header) != 84:
        raise ValueError(f"STL header is too short: {path}")
    triangle_count = struct.unpack_from("<I", header, 80)[0]
    expected_size = 84 + triangle_count * STL_TRIANGLE_DTYPE.itemsize
    if path.stat().st_size != expected_size:
        raise ValueError("Only binary STL files are supported")
    return triangle_count


def build_component_ids(path: Path) -> np.ndarray:
    triangle_count = read_triangle_count(path)
    triangles = np.memmap(
        path,
        dtype=STL_TRIANGLE_DTYPE,
        mode="r",
        offset=84,
        shape=(triangle_count,),
    )
    vertices = np.asarray(triangles["vertices"]).reshape(-1, 3)
    _, vertex_ids = np.unique(vertices, axis=0, return_inverse=True)

    order = np.argsort(vertex_ids, kind="stable")
    sorted_vertex_ids = vertex_ids[order]
    sorted_triangle_ids = (order // 3).astype(np.int32)

    parent = np.arange(triangle_count, dtype=np.int32)
    sizes = np.ones(triangle_count, dtype=np.int32)

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = int(parent[item])
        return item

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if sizes[left_root] < sizes[right_root]:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        sizes[left_root] += sizes[right_root]

    previous_vertex = int(sorted_vertex_ids[0])
    previous_triangle = int(sorted_triangle_ids[0])
    for index in range(1, len(order)):
        vertex = int(sorted_vertex_ids[index])
        triangle = int(sorted_triangle_ids[index])
        if vertex == previous_vertex and triangle != previous_triangle:
            union(previous_triangle, triangle)
        previous_vertex = vertex
        previous_triangle = triangle

    roots = np.empty(triangle_count, dtype=np.int32)
    for triangle in range(triangle_count):
        roots[triangle] = find(triangle)
    _, component_ids = np.unique(roots, return_inverse=True)
    if component_ids.max(initial=0) > np.iinfo(np.uint16).max:
        raise ValueError("Component count exceeds uint16 capacity")
    return component_ids.astype("<u2")


def write_component_map(path: Path, component_ids: np.ndarray) -> None:
    component_count = int(component_ids.max(initial=0)) + 1
    with path.open("wb") as handle:
        handle.write(b"HCP1")
        handle.write(struct.pack("<II", len(component_ids), component_count))
        handle.write(component_ids.tobytes())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build triangle-to-connected-component map for a binary STL."
    )
    parser.add_argument("stl", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    started = time.monotonic()
    component_ids = build_component_ids(args.stl)
    write_component_map(args.output, component_ids)
    counts = np.bincount(component_ids)
    print(
        f"triangles={len(component_ids)} components={len(counts)} "
        f"largest={int(counts.max())} elapsed={time.monotonic() - started:.1f}s"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
