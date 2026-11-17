"""Rebuild the small reader JAR and lock the exact Maven runtime dependencies."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maven-repo", type=Path, default=Path.home() / ".m2" / "repository")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    mvn = shutil.which("mvn")
    if not mvn:
        parser.error("Maven is required to rebuild the reader.")
    local = args.maven_repo.resolve()
    subprocess.run([mvn, "-q", f"-Dmaven.repo.local={local}", "clean", "package",
                    "org.apache.maven.plugins:maven-dependency-plugin:3.8.1:build-classpath",
                    "-Dmdep.outputFile=target/classpath.txt"], cwd=source, check=True)
    reader = source / "coach-reader.jar"
    shutil.copyfile(source / "target/original-replay-decoder.jar", reader)
    libraries = []
    for entry in (source / "target/classpath.txt").read_text().strip().split(os.pathsep):
        jar = Path(entry)
        libraries.append({"path": jar.relative_to(local).as_posix(), "sha256": sha256(jar)})
    bundle = {"reader_sha256": sha256(reader), "libraries": libraries}
    (source / "runtime.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
