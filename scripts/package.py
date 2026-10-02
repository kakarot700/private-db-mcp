"""Package only source, documented examples, built distributions and verification proof."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = ("src", "tests", "examples", "docs", "scripts", ".github", "reports", "dist")
FILES = (
    "README.md",
    "SECURITY.md",
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
    "requirements.lock",
    ".gitignore",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    proof = json.loads((ROOT / "reports/verification.json").read_text())
    if not proof.get("all_checks_passed"):
        raise SystemExit("Refusing to package without passing verification.")
    paths = [ROOT / name for name in FILES]
    for directory in DIRS:
        paths.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    paths = sorted(set(paths))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, Path(ROOT.name) / path.relative_to(ROOT))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    checksum = args.output.with_suffix(args.output.suffix + ".sha256")
    checksum.write_text(f"{digest}  {args.output.name}\n")
    print(f"Packaged {len(paths)} files: {args.output.name}")
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()
