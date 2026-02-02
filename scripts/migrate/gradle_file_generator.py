"""Gradle file generators.

Produces content for build.gradle.kts, settings.gradle.kts,
gradle/libs.versions.toml, gradle.properties, and .gitignore entries.
All functions take parsed Maven data as input and return strings.
"""

import sys
from collections import OrderedDict
from typing import Optional

from .pom_models import Dependency, MavenModule
from .pom_parser import resolve_property, is_bom_import
from .maven_gradle_mappings import (
    PLUGIN_ID_MAP, PLUGIN_SKIP,
    to_alias, to_version_key, to_plugin_alias, gradle_config,
)
from .tech_stack_detector import (
    detect_java_version, detect_kotlin_version,
    is_spring_boot_project, is_devtools,
)


def build_version_catalog(
    root_module: MavenModule,
    child_modules: list[MavenModule],
) -> str:
    """Build a ``libs.versions.toml`` content string from parsed Maven modules.

    Produces three TOML sections:
        - ``[versions]``  — version constants (Spring Boot, Java, Kotlin, etc.)
        - ``[libraries]`` — dependency aliases with group/name/version.ref
        - ``[plugins]``   — Gradle plugin aliases with id/version.ref

    Dependencies with unresolvable ``${...}`` versions are commented out with
    a ``# TODO`` marker instead of producing invalid TOML.

    Args:
        root_module: The parsed root pom.xml module.
        child_modules: List of parsed child module pom.xml files.

    Returns:
        A complete ``libs.versions.toml`` file content as a string.
    """
    all_modules = [root_module] + child_modules
    all_properties = dict(root_module.properties)
    for m in child_modules:
        all_properties.update(m.properties)

    # Inter-module artifact IDs to skip
    module_artifact_ids = {root_module.artifact_id}
    for cm in child_modules:
        module_artifact_ids.add(cm.artifact_id)

    versions = OrderedDict()      # version-ref → version string
    libraries = OrderedDict()     # alias → toml line
    plugins_section = OrderedDict()  # alias → toml line

    # Track seen coordinates to deduplicate
    seen_libs = set()
    seen_plugins = set()

    # ── Spring Boot special handling ──
    is_boot = is_spring_boot_project(root_module)
    if is_boot and root_module.parent_version:
        resolved_boot_ver = resolve_property(root_module.parent_version, all_properties) or root_module.parent_version
        versions["spring-boot"] = resolved_boot_ver

    # ── Java / Kotlin versions (stored as versions for reference) ──
    java_ver = detect_java_version(all_properties, root_module.plugins + root_module.plugin_management)
    if java_ver:
        versions["java"] = java_ver

    kotlin_ver = detect_kotlin_version(all_properties, root_module.plugins + root_module.plugin_management)
    if kotlin_ver:
        versions["kotlin"] = kotlin_ver

    # ── Collect BOMs from dependencyManagement ──
    for mod in all_modules:
        for dep in mod.dep_management:
            if is_bom_import(dep):
                coord = (dep.group_id, dep.artifact_id)
                if coord in seen_libs:
                    continue
                seen_libs.add(coord)
                alias = to_alias(dep.group_id, dep.artifact_id)
                ver = resolve_property(dep.version, all_properties) if dep.version else None
                if ver and ver.startswith("$"):
                    # Unresolvable property — comment out to avoid invalid TOML
                    print(f"WARNING: Could not resolve version '{dep.version}' for "
                          f"{dep.group_id}:{dep.artifact_id}, commenting out in catalog",
                          file=sys.stderr)
                    libraries[alias] = (
                        f'# {{ group = "{dep.group_id}", name = "{dep.artifact_id}" }}'
                        f"  # TODO: resolve version from {dep.version}"
                    )
                elif ver:
                    vref = to_version_key(alias)
                    versions[vref] = ver
                    libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}", version.ref = "{vref}" }}'
                else:
                    libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}" }}'

    # ── Collect non-BOM managed dependencies (these set default versions) ──
    managed_versions = {}  # (groupId, artifactId) → version
    for mod in all_modules:
        for dep in mod.dep_management:
            if not is_bom_import(dep) and dep.version:
                ver = resolve_property(dep.version, all_properties) or dep.version
                managed_versions[(dep.group_id, dep.artifact_id)] = ver

    # ── Collect all dependencies ──
    for mod in all_modules:
        for dep in mod.dependencies:
            coord = (dep.group_id, dep.artifact_id)
            if coord in seen_libs:
                continue
            # Skip inter-module dependencies
            if dep.artifact_id in module_artifact_ids and dep.group_id == root_module.group_id:
                continue
            seen_libs.add(coord)
            alias = to_alias(dep.group_id, dep.artifact_id)

            # Resolve version: explicit > managed > property
            ver = None
            if dep.version:
                ver = resolve_property(dep.version, all_properties) or dep.version
            elif coord in managed_versions:
                ver = managed_versions[coord]

            if ver and ver.startswith("$"):
                # Unresolvable property — comment out
                print(f"WARNING: Could not resolve version '{ver}' for "
                      f"{dep.group_id}:{dep.artifact_id}, commenting out in catalog",
                      file=sys.stderr)
                libraries[alias] = (
                    f'# {{ group = "{dep.group_id}", name = "{dep.artifact_id}" }}'
                    f"  # TODO: resolve version from {ver}"
                )
            elif ver:
                vref = to_version_key(alias)
                # Deduplicate version refs if same version already tracked
                existing_vref = None
                for k, v in versions.items():
                    if v == ver and k != "java":
                        existing_vref = k
                        break
                if existing_vref:
                    vref = existing_vref
                else:
                    versions[vref] = ver
                libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}", version.ref = "{vref}" }}'
            else:
                # Version managed by BOM or Spring Boot parent — no version in catalog
                libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}" }}'

    # ── Collect plugins ──
    if is_boot:
        plugins_section["spring-boot"] = f'{{ id = "org.springframework.boot", version.ref = "spring-boot" }}'
        # spring-dependency-management version is a Gradle-only concept — omit
        # version to let the Spring Boot plugin manage compatibility
        plugins_section["spring-dependency-management"] = '{ id = "io.spring.dependency-management" }'
        seen_plugins.add("spring-boot-maven-plugin")

    if kotlin_ver:
        plugins_section["kotlin-jvm"] = f'{{ id = "org.jetbrains.kotlin.jvm", version.ref = "kotlin" }}'
        plugins_section["kotlin-spring"] = f'{{ id = "org.jetbrains.kotlin.plugin.spring", version.ref = "kotlin" }}'
        seen_plugins.add("kotlin-maven-plugin")

    for mod in all_modules:
        for p in mod.plugins + mod.plugin_management:
            if p.artifact_id in seen_plugins or p.artifact_id in PLUGIN_SKIP:
                continue
            seen_plugins.add(p.artifact_id)
            gradle_id = PLUGIN_ID_MAP.get(p.artifact_id)
            if not gradle_id:
                continue
            alias = to_plugin_alias(p.group_id, p.artifact_id)
            ver = resolve_property(p.version, all_properties) if p.version else None
            if ver and ver.startswith("$"):
                print(f"WARNING: Could not resolve plugin version '{p.version}' for "
                      f"{p.artifact_id}, omitting version in catalog",
                      file=sys.stderr)
                plugins_section[alias] = f'{{ id = "{gradle_id}" }}'
            elif ver:
                vref = to_version_key(alias)
                versions[vref] = ver
                plugins_section[alias] = f'{{ id = "{gradle_id}", version.ref = "{vref}" }}'
            else:
                plugins_section[alias] = f'{{ id = "{gradle_id}" }}'

    # ── Render TOML ──
    lines = ["[versions]"]
    for k, v in versions.items():
        lines.append(f'{k} = "{v}"')

    lines.append("")
    lines.append("[libraries]")
    for alias, definition in libraries.items():
        lines.append(f"{alias} = {definition}")

    if plugins_section:
        lines.append("")
        lines.append("[plugins]")
        for alias, definition in plugins_section.items():
            lines.append(f"{alias} = {definition}")

    lines.append("")
    return "\n".join(lines)


def _is_inter_module_dep(dep: Dependency, root_module: MavenModule, child_modules: list) -> Optional[str]:
    """Check if a dependency refers to another module in the same multi-module project.

    Matches by artifactId + groupId against the root and all child modules.

    Args:
        dep: The dependency to check.
        root_module: The root MavenModule.
        child_modules: List of child MavenModule instances.

    Returns:
        The module's source directory name if it's an inter-module dependency,
        or ``None`` if it's an external dependency.
    """
    all_artifact_ids = {root_module.artifact_id: "."}
    for cm in child_modules:
        all_artifact_ids[cm.artifact_id] = cm.source_dir or cm.artifact_id
    if dep.artifact_id in all_artifact_ids and dep.group_id == root_module.group_id:
        return all_artifact_ids[dep.artifact_id]
    return None


def generate_build_gradle_kts(
    module: MavenModule,
    root_module: MavenModule,
    is_root: bool = True,
    is_multi_module: bool = False,
    child_modules: list = None,

) -> str:
    """Generate ``build.gradle.kts`` content for a single module.

    Produces a complete build file including plugins, group/version, Java
    toolchain, Kotlin compiler options, configurations, repositories,
    dependencies, allprojects/subprojects blocks, test config, and profile
    conversion hints.

    Args:
        module: The MavenModule to generate a build file for.
        root_module: The root MavenModule (used for Spring Boot detection, etc.).
        is_root: Whether this is the root module.
        is_multi_module: Whether the project is multi-module.
        child_modules: List of child MavenModule instances (for inter-module deps).

    Returns:
        Complete ``build.gradle.kts`` file content as a string.
    """
    lines = []
    all_properties = dict(root_module.properties)
    all_properties.update(module.properties)

    is_boot = is_spring_boot_project(root_module)
    has_kotlin = detect_kotlin_version(all_properties, root_module.plugins + root_module.plugin_management) is not None
    java_ver = detect_java_version(all_properties, root_module.plugins + root_module.plugin_management)

    # ── Plugins block ──
    if is_root or not is_multi_module:
        lines.append("plugins {")

        if module.packaging == "pom" and is_multi_module:
            # Root POM in multi-module — plugins applied with apply false
            if is_boot:
                lines.append("    alias(libs.plugins.spring.boot) apply false")
                lines.append("    alias(libs.plugins.spring.dependency.management) apply false")
            if has_kotlin:
                lines.append("    alias(libs.plugins.kotlin.jvm) apply false")
                lines.append("    alias(libs.plugins.kotlin.spring) apply false")
        else:
            # Apply Java or Kotlin plugin
            if has_kotlin:
                lines.append("    alias(libs.plugins.kotlin.jvm)")
                lines.append("    alias(libs.plugins.kotlin.spring)")
            else:
                lines.append("    java")

            if is_boot:
                lines.append("    alias(libs.plugins.spring.boot)")
                lines.append("    alias(libs.plugins.spring.dependency.management)")

        # Additional plugins
        for p in module.plugins:
            if p.artifact_id in PLUGIN_SKIP or p.artifact_id in (
                "spring-boot-maven-plugin", "kotlin-maven-plugin"
            ):
                continue
            gradle_id = PLUGIN_ID_MAP.get(p.artifact_id)
            if gradle_id:
                alias = to_plugin_alias(p.group_id, p.artifact_id)
                safe_alias = alias.replace("-", ".")
                lines.append(f"    alias(libs.plugins.{safe_alias})")

        lines.append("}")
        lines.append("")
    else:
        # Child module in multi-module
        lines.append("plugins {")
        if has_kotlin:
            lines.append("    alias(libs.plugins.kotlin.jvm)")
            lines.append("    alias(libs.plugins.kotlin.spring)")
        else:
            lines.append("    java")
        if is_boot:
            lines.append("    alias(libs.plugins.spring.boot)")
            lines.append("    alias(libs.plugins.spring.dependency.management)")
        for p in module.plugins:
            if p.artifact_id in PLUGIN_SKIP or p.artifact_id in (
                "spring-boot-maven-plugin", "kotlin-maven-plugin"
            ):
                continue
            gradle_id = PLUGIN_ID_MAP.get(p.artifact_id)
            if gradle_id:
                alias = to_plugin_alias(p.group_id, p.artifact_id)
                safe_alias = alias.replace("-", ".")
                lines.append(f"    alias(libs.plugins.{safe_alias})")
        lines.append("}")
        lines.append("")

    # ── Group / Version ──
    if is_root or not is_multi_module:
        lines.append(f'group = "{module.group_id}"')
        if module.version:
            lines.append(f'version = "{module.version}"')
        lines.append("")

    # ── Java toolchain ──
    if java_ver and not (is_multi_module and module.packaging == "pom"):
        lines.append("java {")
        lines.append("    toolchain {")
        lines.append(f"        languageVersion = JavaLanguageVersion.of({java_ver})")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # ── Kotlin compiler options ──
    if has_kotlin and not (is_multi_module and module.packaging == "pom"):
        lines.append("kotlin {")
        lines.append("    compilerOptions {")
        lines.append("        freeCompilerArgs.addAll(\"-Xjsr305=strict\")")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # ── Configurations (for optional/compileOnly patterns) ──
    has_annotation_processor = any(
        d.scope == "provided" or d.artifact_id in ("lombok", "mapstruct-processor")
        for d in module.dependencies
    )

    if has_annotation_processor and has_kotlin:
        lines.append("configurations {")
        lines.append("    compileOnly {")
        lines.append("        extendsFrom(configurations.annotationProcessor.get())")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # ── Repositories ──
    if is_root:
        lines.append("repositories {")
        lines.append("    mavenCentral()")
        lines.append("}")
        lines.append("")

    # ── Dependencies ──
    if module.dependencies and not (is_multi_module and module.packaging == "pom" and is_root):
        lines.append("dependencies {")

        # BOMs from dependencyManagement
        for dep in module.dep_management:
            if is_bom_import(dep):
                alias = to_alias(dep.group_id, dep.artifact_id)
                safe_alias = alias.replace("-", ".")
                lines.append(f"    implementation(platform(libs.{safe_alias}))")

        for dep in module.dependencies:
            # Check if inter-module dependency
            mod_dir = _is_inter_module_dep(dep, root_module, child_modules or [])
            if mod_dir:
                config = gradle_config(dep.scope)
                lines.append(f'    {config}(project(":{dep.artifact_id}"))')
                continue

            alias = to_alias(dep.group_id, dep.artifact_id)
            safe_alias = alias.replace("-", ".")
            config = gradle_config(dep.scope)

            # DevTools → developmentOnly
            if is_devtools(dep):
                lines.append(f"    developmentOnly(libs.{safe_alias})")
                continue

            # Annotation processors
            is_apt = dep.artifact_id in (
                "lombok", "mapstruct-processor", "hibernate-jpamodelgen",
                "spring-boot-configuration-processor",
            )

            if is_apt:
                dep_ref = f"libs.{safe_alias}"
                if dep.scope == "test":
                    # Test-only annotation processor
                    lines.append(f"    testCompileOnly({dep_ref})")
                    lines.append(f"    testAnnotationProcessor({dep_ref})")
                else:
                    if dep.scope == "provided" or dep.optional:
                        lines.append(f"    compileOnly({dep_ref})")
                    lines.append(f"    annotationProcessor({dep_ref})")
            elif dep.exclusions:
                lines.append(f"    {config}(libs.{safe_alias}) {{")
                for eg, ea in dep.exclusions:
                    lines.append(f'        exclude(group = "{eg}", module = "{ea}")')
                lines.append("    }")
            else:
                if dep.optional and config == "implementation":
                    config = "compileOnly"
                lines.append(f"    {config}(libs.{safe_alias})")

        lines.append("}")
        lines.append("")

    # ── allprojects / subprojects for multi-module root ──
    if is_multi_module and module.packaging == "pom" and is_root:
        lines.append("allprojects {")
        lines.append(f'    group = "{module.group_id}"')
        if module.version:
            lines.append(f'    version = "{module.version}"')
        lines.append("}")
        lines.append("")
        lines.append("subprojects {")
        lines.append("    repositories {")
        lines.append("        mavenCentral()")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # ── Test configuration ──
    has_tests = any(d.scope == "test" for d in module.dependencies)
    if has_tests and not (is_multi_module and module.packaging == "pom"):
        lines.append("tasks.withType<Test> {")
        lines.append("    useJUnitPlatform()")
        lines.append("}")
        lines.append("")

    # ── Profile conversion hints (as comments) ──
    if module.profiles:
        lines.append("// ── Maven profile equivalents ─────────────────────────────────")
        lines.append("// See references/profiles.md in the migration skill for patterns.")
        for prof in module.profiles:
            lines.append(f"// Profile '{prof.profile_id}':")
            if prof.activation.get("activeByDefault"):
                lines.append("//   → Active by default: apply unconditionally or use a Gradle property")
            if "property" in prof.activation:
                prop = prof.activation["property"]
                lines.append(f'//   → Activated by property: -P{prop.get("name", "?")}={prop.get("value", "")}')
                lines.append(f'//   → Gradle equivalent: if (project.hasProperty("{prop.get("name", "")}")) {{ ... }}')
            if "jdk" in prof.activation:
                lines.append(f"//   → JDK activation: {prof.activation['jdk']}")
            if prof.dependencies:
                lines.append(f"//   → Has {len(prof.dependencies)} dependencies")
            if prof.plugins:
                lines.append(f"//   → Has {len(prof.plugins)} plugins")
        lines.append("")

    return "\n".join(lines)


def generate_settings_gradle_kts(
    root_module: MavenModule,
    child_modules: list[MavenModule] = None,
) -> str:
    """Generate ``settings.gradle.kts`` content with repository and module configuration.

    When custom repositories are detected (beyond Maven Central), generates
    ``pluginManagement`` and ``dependencyResolutionManagement`` blocks.
    Well-known repository URLs (e.g. Spring milestones) are mapped to
    descriptive names.

    For nested multi-module projects, module includes use colon-separated
    Gradle paths (e.g. ``include("parent:child")``).

    Args:
        root_module: The parsed root MavenModule.
        child_modules: Optional list of all child modules (for repository aggregation
            and nested module include paths).

    Returns:
        Complete ``settings.gradle.kts`` file content as a string.
    """
    lines = []
    child_modules = child_modules or []

    # Collect all custom repositories from root + children
    all_repos = list(root_module.repositories)
    for cm in child_modules:
        all_repos.extend(cm.repositories)

    # Deduplicate by URL
    seen_urls = set()
    unique_repos = []
    for repo_id, repo_url in all_repos:
        normalized = repo_url.rstrip("/")
        if normalized not in seen_urls and "repo1.maven.org" not in normalized and "central" not in repo_id.lower():
            seen_urls.add(normalized)
            unique_repos.append((repo_id, normalized))

    has_custom_repos = bool(unique_repos)

    # Generate pluginManagement block (always for multi-module or custom repos)
    if has_custom_repos or root_module.modules:
        lines.append("pluginManagement {")
        lines.append("    repositories {")
        lines.append("        mavenCentral()")
        lines.append("        gradlePluginPortal()")
        for _repo_id, repo_url in unique_repos:
            lines.append(f'        maven {{ url = uri("{repo_url}") }}')
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # Generate dependencyResolutionManagement block if custom repos exist
    if has_custom_repos:
        lines.append("dependencyResolutionManagement {")
        lines.append("    repositories {")
        lines.append("        mavenCentral()")
        for _repo_id, repo_url in unique_repos:
            lines.append(f'        maven {{ url = uri("{repo_url}") }}')
        lines.append("    }")
        lines.append("}")
        lines.append("")

    project_name = root_module.artifact_id
    lines.append(f'rootProject.name = "{project_name}"')
    lines.append("")

    if child_modules:
        for child in child_modules:
            # Convert filesystem path (a/b) to Gradle include path (a:b)
            gradle_path = child.source_dir.replace("/", ":")
            lines.append(f'include("{gradle_path}")')
        lines.append("")

    return "\n".join(lines)


def generate_gradle_properties(root_module: MavenModule) -> str:
    """Generate ``gradle.properties`` content with build performance settings.

    Enables Gradle daemon, parallel execution, and local build caching.
    Configuration cache is included as a commented-out suggestion since not
    all plugins support it.

    Custom Maven properties (excluding standard Maven/Java/Kotlin prefixes)
    are carried over as comments for reference.

    Args:
        root_module: The root MavenModule (for custom property extraction).

    Returns:
        Complete ``gradle.properties`` file content as a string.
    """
    lines = [
        "# Generated by Maven-to-Gradle migration",
        "org.gradle.daemon=true",
        "org.gradle.parallel=true",
        "org.gradle.caching=true",
        "# org.gradle.configuration-cache=true  # Enable after verifying all plugins support it",
    ]
    # Carry over relevant Maven properties
    for key, value in root_module.properties.items():
        if key.startswith("project.build.sourceEncoding"):
            lines.append(f"# Source encoding: {value}")
        elif key.startswith("project.reporting.outputEncoding"):
            continue
        elif not key.startswith("maven.") and not key.startswith("java.") and not key.startswith("kotlin."):
            # Custom properties — include as Gradle project properties
            safe_key = key.replace(".", "_")
            lines.append(f"# {safe_key}={value}")
    lines.append("")
    return "\n".join(lines)


def generate_gradle_gitignore_entries() -> str:
    """Generate ``.gitignore`` entries for Gradle build artifacts.

    Returns:
        A string of gitignore rules covering ``.gradle/``, ``build/``,
        and the Gradle wrapper JAR exception.
    """
    return """\
# Gradle
.gradle/
build/
!gradle/wrapper/gradle-wrapper.jar
!**/src/main/**/build/
!**/src/test/**/build/
"""
