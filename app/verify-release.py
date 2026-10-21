"""Check program integrity; local content checking is explicit and optional."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def verify(root, manifest, prefix=""):
    root = root.resolve()
    count, size = 0, 0
    for name, row in manifest["files"].items():
        relative = PurePosixPath(prefix + name)
        if relative.is_absolute() or ".." in relative.parts or ":" in str(relative) or "\\" in str(relative):
            raise ValueError("Unsafe package path")
        path = root.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError(f"Missing file: {relative}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if path.stat().st_size != row["size"] or digest.hexdigest() != row["sha256"]:
            raise ValueError(f"File checksum mismatch: {relative}")
        count += 1; size += row["size"]
        if count % 2000 == 0:
            print(f"Checked {count} files", flush=True)
    return count, size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--include-content", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    program = json.loads((root / "release.json").read_text(encoding="utf-8"))
    if program.get("kind") != "fkg-reader-program" or program.get("formatVersion") != 1 or (root / "BUILDING.json").exists():
        raise ValueError("Invalid/incomplete program package")
    count, size = verify(root, program)
    result = {"ok": True, "programFiles": count, "programBytes": size, "contentChecked": False}
    if args.include_content:
        content = json.loads((root / "content/manifest.json").read_text(encoding="utf-8"))
        if content.get("kind") != "fkg-reader-content" or content.get("formatVersion") != 1:
            raise ValueError("Unsupported local content")
        result["contentFiles"], result["contentBytes"] = verify(root, content, "content/")
        result["contentChecked"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
