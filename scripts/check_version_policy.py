#!/usr/bin/env python3
"""Validate monorepo package version policy.

Rules enforced:
1. Every package under packages/* must have a declared version and a runtime
   __version__ that match.
2. If a package's runtime/build-impacting files change relative to --base-ref,
    that package's declared version must change.
3. If any package has runtime/build-impacting changes relative to --base-ref,
    the root horde-exporters version must change.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for Python < 3.11
    import tomli as tomllib


RE_VERSION_ASSIGN = re.compile(
    r"(?m)^\s*__version__\s*=\s*['\"](?P<version>[^'\"]+)['\"]\s*$"
)
RE_REEXPORT = re.compile(
    r"(?m)^\s*from\s+\.(?P<module>[A-Za-z_]\w*)\s+import\s+__version__\s*$"
)
RE_REQUIREMENTS_FILE = re.compile(r"^(requirements|constraints)(-.+)?\.txt$")

BUILD_RELATED_FILENAMES = {
    "pyproject.toml",
    "uv.lock",
}


class PolicyError(RuntimeError):
    """Raised when a version policy cannot be evaluated for an item."""


@dataclass
class PackageInfo:
    dir_name: str
    distribution_name: str
    import_name: str
    declared_version: str
    runtime_version: str


def run_git(repo_root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise PolicyError(
            f"git {' '.join(args)} failed with exit code {proc.returncode}:"
            f" {proc.stderr.strip()}"
        )
    return proc.stdout


def extract_assigned_version(text: str, source_name: str) -> str:
    match = RE_VERSION_ASSIGN.search(text)
    if not match:
        raise PolicyError(
            f"No __version__ assignment found in {source_name}. "
            "Expected: __version__ = \"x.y.z\""
        )
    return match.group("version").strip()


def load_toml_from_path(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def resolve_declared_version(
    pyproject: dict,
    read_rel_text: Callable[[str], str],
) -> str:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise PolicyError("Missing [project] table")

    static_version = project.get("version")
    if isinstance(static_version, str) and static_version.strip():
        return static_version.strip()

    dynamic = project.get("dynamic")
    if isinstance(dynamic, list) and "version" in dynamic:
        tool = pyproject.get("tool", {})
        hatch = tool.get("hatch", {}) if isinstance(tool, dict) else {}
        hatch_version = hatch.get("version", {}) if isinstance(hatch, dict) else {}
        path = hatch_version.get("path") if isinstance(hatch_version, dict) else None
        if not isinstance(path, str) or not path.strip():
            raise PolicyError(
                "project.dynamic includes 'version' but "
                "tool.hatch.version.path is missing"
            )
        version_text = read_rel_text(path)
        return extract_assigned_version(version_text, path)

    raise PolicyError(
        "No supported version declaration found; use project.version or "
        "project.dynamic + tool.hatch.version.path"
    )


def resolve_runtime_version(package_dir: Path, import_name: str) -> str:
    src_root = package_dir / "src" / import_name
    init_path = src_root / "__init__.py"
    if not init_path.exists():
        raise PolicyError(f"Missing runtime package file: {init_path}")

    init_text = init_path.read_text(encoding="utf-8")

    direct_match = RE_VERSION_ASSIGN.search(init_text)
    if direct_match:
        return direct_match.group("version").strip()

    reexport_match = RE_REEXPORT.search(init_text)
    if reexport_match:
        module_name = reexport_match.group("module")
        module_path = src_root / f"{module_name}.py"
        if not module_path.exists():
            raise PolicyError(
                f"{init_path} re-exports __version__ from .{module_name}, "
                f"but {module_path} does not exist"
            )
        module_text = module_path.read_text(encoding="utf-8")
        return extract_assigned_version(module_text, str(module_path.relative_to(package_dir)))

    raise PolicyError(
        f"Could not resolve runtime __version__ from {init_path}; "
        "define it directly or re-export from a local module"
    )


def discover_packages(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "packages").glob("*/pyproject.toml"))


def package_info_from_head(package_pyproject: Path) -> PackageInfo:
    package_dir = package_pyproject.parent
    pyproject = load_toml_from_path(package_pyproject)

    project = pyproject.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        raise PolicyError(f"Missing project.name in {package_pyproject}")

    distribution_name = project["name"].strip()
    import_name = distribution_name.replace("-", "_")

    declared_version = resolve_declared_version(
        pyproject,
        lambda rel: (package_dir / rel).read_text(encoding="utf-8"),
    )
    runtime_version = resolve_runtime_version(package_dir, import_name)

    return PackageInfo(
        dir_name=package_dir.name,
        distribution_name=distribution_name,
        import_name=import_name,
        declared_version=declared_version,
        runtime_version=runtime_version,
    )


def package_declared_version_from_ref(
    repo_root: Path,
    base_ref: str,
    package_dir_name: str,
) -> str | None:
    pyproject_rel = f"packages/{package_dir_name}/pyproject.toml"
    proc = subprocess.run(
        ["git", "show", f"{base_ref}:{pyproject_rel}"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None

    pyproject = tomllib.loads(proc.stdout)

    def read_rel_text(rel_path: str) -> str:
        rel = f"packages/{package_dir_name}/{rel_path}"
        rel_proc = subprocess.run(
            ["git", "show", f"{base_ref}:{rel}"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if rel_proc.returncode != 0:
            raise PolicyError(
                f"Failed to read dynamic version source {rel} at {base_ref}: "
                f"{rel_proc.stderr.strip()}"
            )
        return rel_proc.stdout

    return resolve_declared_version(pyproject, read_rel_text)


def root_version_from_head(repo_root: Path) -> str:
    pyproject = load_toml_from_path(repo_root / "pyproject.toml")
    return resolve_declared_version(
        pyproject,
        lambda rel: (repo_root / rel).read_text(encoding="utf-8"),
    )


def root_version_from_ref(repo_root: Path, base_ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{base_ref}:pyproject.toml"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    pyproject = tomllib.loads(proc.stdout)

    def read_rel_text(rel_path: str) -> str:
        rel_proc = subprocess.run(
            ["git", "show", f"{base_ref}:{rel_path}"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if rel_proc.returncode != 0:
            raise PolicyError(
                f"Failed to read root dynamic version source {rel_path} at {base_ref}: "
                f"{rel_proc.stderr.strip()}"
            )
        return rel_proc.stdout

    return resolve_declared_version(pyproject, read_rel_text)


def package_change_requires_version_bump(path: str) -> bool:
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "packages":
        return False

    rel_parts = parts[2:]
    rel_path = "/".join(rel_parts)
    file_name = rel_parts[-1]

    # Anything under src/ potentially changes runtime behavior or bundled output.
    if rel_parts[0] == "src":
        return True

    # Build metadata and dependency declarations can alter wheel/sdist output.
    if rel_path in BUILD_RELATED_FILENAMES:
        return True
    if RE_REQUIREMENTS_FILE.match(file_name):
        return True

    # Python build helpers at package root/subdirs can affect effective output.
    if file_name.endswith(".py") and "tests" not in rel_parts:
        return True

    return False


def changed_package_dirs(repo_root: Path, base_ref: str) -> dict[str, list[str]]:
    changed = run_git(
        repo_root,
        "diff",
        "--name-only",
        "--diff-filter=ACMRD",
        f"{base_ref}...HEAD",
    )
    package_dirs: dict[str, list[str]] = {}
    for line in changed.splitlines():
        parts = line.split("/")
        if len(parts) >= 2 and parts[0] == "packages":
            if not package_change_requires_version_bump(line):
                continue
            package_dirs.setdefault(parts[1], []).append(line)
    return package_dirs


def ensure_ref_exists(repo_root: Path, ref: str) -> None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PolicyError(
            f"Base ref '{ref}' not found. Ensure CI fetch depth includes the base branch."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate horde-exporters version policy")
    parser.add_argument(
        "--base-ref",
        required=True,
        help="Git ref to compare against (example: origin/main)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    violations: list[str] = []

    try:
        ensure_ref_exists(repo_root, args.base_ref)

        package_infos: dict[str, PackageInfo] = {}
        for package_pyproject in discover_packages(repo_root):
            info = package_info_from_head(package_pyproject)
            package_infos[info.dir_name] = info
            if info.declared_version != info.runtime_version:
                violations.append(
                    "Package "
                    f"{info.dir_name}: declared version '{info.declared_version}' "
                    f"does not match runtime __version__ '{info.runtime_version}'"
                )

        changed_packages = changed_package_dirs(repo_root, args.base_ref)

        if changed_packages:
            head_root_version = root_version_from_head(repo_root)
            base_root_version = root_version_from_ref(repo_root, args.base_ref)
            if base_root_version is None:
                violations.append(
                    "Could not read root pyproject.toml from base ref for parent version check"
                )
            elif head_root_version == base_root_version:
                violations.append(
                    "Root project version in pyproject.toml must change whenever runtime/build "
                    f"package files change. Changed packages: {', '.join(sorted(changed_packages))}"
                )

            for pkg in sorted(changed_packages):
                head_info = package_infos.get(pkg)
                if head_info is None:
                    # Package may have been deleted; parent bump check above still applies.
                    continue

                base_version = package_declared_version_from_ref(repo_root, args.base_ref, pkg)
                if base_version is None:
                    # New package introduced in this branch.
                    continue
                if head_info.declared_version == base_version:
                    changed_items = ", ".join(changed_packages[pkg])
                    violations.append(
                        f"Package {pkg} has runtime/build-impacting changes ({changed_items}) "
                        f"but declared version stayed '{head_info.declared_version}'. "
                        "Bump the package version."
                    )

    except PolicyError as exc:
        print(f"Version policy check failed: {exc}", file=sys.stderr)
        return 2

    if violations:
        print("Version policy violations detected:", file=sys.stderr)
        for item in violations:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("Version policy checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
