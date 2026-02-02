"""CLI entry point, multi-module orchestration, and file I/O.

Wires together parsing, mapping, and generation to execute the full
Maven-to-Gradle migration pipeline.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from .models import MavenModule
from .maven import parse_pom
from .mapping import to_alias
from .gradle import (
    build_version_catalog,
    generate_build_gradle_kts,
    generate_settings_gradle_kts,
    generate_gradle_properties,
    generate_gradle_gitignore_entries,
)


def _parse_modules_recursive(
    project_path: Path,
    module_dirs: list[str],
    parent_path: str = "",
    _visited: set = None,
) -> list[MavenModule]:
    """Recursively parse child modules, handling nested multi-module structures.

    If a child module itself declares ``<modules>``, those nested modules are
    also parsed and included in the result. Visited paths are tracked to
    prevent infinite recursion from circular module references.

    Args:
        project_path: Filesystem path to the root project.
        module_dirs: List of module directory names from the parent's ``<modules>``.
        parent_path: The relative path prefix for nested modules (e.g. ``"parent-mod"``).
        _visited: Internal set of visited paths (callers should not set this).

    Returns:
        Flat list of all MavenModule instances (depth-first), with ``source_dir``
        set to the relative filesystem path from the project root.
    """
    if _visited is None:
        _visited = set()

    result = []
    for mod_dir in module_dirs:
        relative_dir = f"{parent_path}/{mod_dir}" if parent_path else mod_dir
        # Guard against circular references
        abs_path = (project_path / relative_dir).resolve()
        if abs_path in _visited:
            continue
        _visited.add(abs_path)

        child_pom = project_path / relative_dir / "pom.xml"
        if child_pom.exists():
            child = parse_pom(child_pom)
            child.source_dir = relative_dir
            result.append(child)
            # Recurse into nested modules
            if child.modules:
                nested = _parse_modules_recursive(
                    project_path, child.modules, relative_dir, _visited
                )
                result.extend(nested)
        else:
            print(f"WARNING: Module '{relative_dir}' has no pom.xml, skipping",
                  file=sys.stderr)
    return result


def migrate(project_path: Path, output_path: Optional[Path] = None, dry_run: bool = False, mode: str = "migrate"):
    """Run the full Maven-to-Gradle migration.

    Orchestrates the entire migration pipeline: parses all pom.xml files,
    generates the version catalog, settings, build files, and properties,
    then either prints (dry-run) or writes the output.

    Args:
        project_path: Filesystem path to the Maven project root (containing pom.xml).
        output_path: Directory to write generated files to. Defaults to ``project_path``.
        dry_run: If ``True``, prints generated content to stdout instead of writing files.
        mode: ``"migrate"`` for full migration, ``"overlay"`` for dual-build (keeps Maven).
    """
    root_pom = project_path / "pom.xml"
    if not root_pom.exists():
        print(f"ERROR: No pom.xml found at {root_pom}", file=sys.stderr)
        sys.exit(1)

    out = output_path or project_path
    root_module = parse_pom(root_pom)
    root_module.source_dir = "."

    is_multi = bool(root_module.modules)

    # Parse child modules (recursively for nested multi-module projects)
    child_modules = []
    if is_multi:
        child_modules = _parse_modules_recursive(project_path, root_module.modules)

    # Build catalog alias lookup
    all_deps = root_module.dependencies + root_module.dep_management
    for cm in child_modules:
        all_deps += cm.dependencies + cm.dep_management
    catalog_aliases = {}
    # Track inter-module artifact IDs to exclude from catalog
    module_artifact_ids = {root_module.artifact_id}
    for cm in child_modules:
        module_artifact_ids.add(cm.artifact_id)
    for dep in all_deps:
        if dep.artifact_id not in module_artifact_ids or dep.group_id != root_module.group_id:
            alias = to_alias(dep.group_id, dep.artifact_id)
            catalog_aliases[(dep.group_id, dep.artifact_id)] = alias

    # Generate files
    catalog_content = build_version_catalog(root_module, child_modules)
    settings_content = generate_settings_gradle_kts(root_module, child_modules)
    root_build_content = generate_build_gradle_kts(
        root_module, root_module, catalog_aliases,
        is_root=True, is_multi_module=is_multi, child_modules=child_modules,
    )
    gradle_props = generate_gradle_properties(root_module)

    is_overlay = mode == "overlay"

    if dry_run:
        print("=" * 60)
        print("gradle/libs.versions.toml")
        print("=" * 60)
        print(catalog_content)
        print()
        print("=" * 60)
        print("settings.gradle.kts")
        print("=" * 60)
        print(settings_content)
        print()
        print("=" * 60)
        print("build.gradle.kts (root)")
        print("=" * 60)
        print(root_build_content)
        print()
        print("=" * 60)
        print("gradle.properties")
        print("=" * 60)
        print(gradle_props)

        for child in child_modules:
            child_build = generate_build_gradle_kts(
                child, root_module, catalog_aliases,
                is_root=False, is_multi_module=True, child_modules=child_modules,
            )
            print()
            print("=" * 60)
            print(f"{child.source_dir}/build.gradle.kts")
            print("=" * 60)
            print(child_build)

        if is_overlay:
            print()
            print("=" * 60)
            print(".gitignore (append)")
            print("=" * 60)
            print(generate_gradle_gitignore_entries())
    else:
        _write(out / "gradle" / "libs.versions.toml", catalog_content)
        _write(out / "settings.gradle.kts", settings_content)
        _write(out / "build.gradle.kts", root_build_content)
        _write(out / "gradle.properties", gradle_props)

        for child in child_modules:
            child_build = generate_build_gradle_kts(
                child, root_module, catalog_aliases,
                is_root=False, is_multi_module=True, child_modules=child_modules,
            )
            _write(out / child.source_dir / "build.gradle.kts", child_build)

        if is_overlay:
            # Append Gradle entries to .gitignore
            gitignore_path = out / ".gitignore"
            gitignore_entries = generate_gradle_gitignore_entries()
            if gitignore_path.exists():
                existing = gitignore_path.read_text(encoding="utf-8")
                if ".gradle/" not in existing:
                    with open(gitignore_path, "a", encoding="utf-8") as f:
                        f.write("\n" + gitignore_entries)
                    print(f"  ✓ {gitignore_path} (appended Gradle entries)")
                else:
                    print(f"  ⏭ {gitignore_path} (Gradle entries already present)")
            else:
                _write(gitignore_path, gitignore_entries)

        if is_overlay:
            print(f"\n✅ Gradle overlay complete! Generated files in: {out}")
        else:
            print(f"\n✅ Migration complete! Generated files in: {out}")
        print("\nGenerated files:")
        print("  gradle/libs.versions.toml")
        print("  settings.gradle.kts")
        print("  build.gradle.kts")
        print("  gradle.properties")
        for child in child_modules:
            print(f"  {child.source_dir}/build.gradle.kts")
        print("\n⚠️  Next steps:")
        print("  1. Review generated files and adjust as needed")
        print("  2. Run: gradle wrapper  # uses your installed Gradle version")
        print("  3. Run: ./gradlew build")
        print("  4. Fix any compilation or test issues")
        if is_overlay:
            print("  5. Both Maven and Gradle builds are now available side by side")
            print("     Keep pom.xml and build.gradle.kts in sync when adding dependencies")
            print("     See references/dual-build.md for maintenance guidance")
        else:
            print("  5. Delete pom.xml files once migration is verified")


def _write(path: Path, content: str):
    """Write content to a file, creating parent directories as needed.

    Args:
        path: Filesystem path to write to.
        content: File content string (UTF-8 encoded).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  ✓ {path}")


def main():
    """CLI entry point. Parses arguments and delegates to ``migrate()``."""
    parser = argparse.ArgumentParser(
        description="Migrate Maven project to Gradle KTS with version catalogs"
    )
    parser.add_argument("project", type=Path, help="Path to Maven project root")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output directory (default: project dir)")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Print output without writing files")
    parser.add_argument(
        "--mode", "-m", choices=["migrate", "overlay"], default="migrate",
        help="'migrate' (default) for full migration, 'overlay' for dual-build (keeps Maven)"
    )
    args = parser.parse_args()

    migrate(args.project, args.output, args.dry_run, args.mode)
