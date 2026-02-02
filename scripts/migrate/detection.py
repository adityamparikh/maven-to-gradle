"""Java, Kotlin, and framework detection heuristics.

Inspects parsed Maven data to answer questions about the project's technology
stack, which drives Gradle generation decisions.
"""

from typing import Optional

from .models import Dependency, MavenModule
from .maven import resolve_property


def detect_java_version(properties: dict, plugins: list) -> Optional[str]:
    """Extract the target Java version from Maven properties or compiler plugin config.

    Checks common property names in order of preference, then falls back to
    inspecting the ``maven-compiler-plugin`` ``<configuration>`` block.
    Normalizes legacy ``1.x`` format to just ``x`` (e.g. ``1.8`` → ``8``).

    Args:
        properties: Merged properties dict from all modules.
        plugins: Combined list of plugins and pluginManagement entries.

    Returns:
        Java version string (e.g. ``"21"``), or ``None`` if not detected.
    """
    for prop_name in [
        "java.version", "maven.compiler.release", "maven.compiler.source",
        "maven.compiler.target", "jdk.version", "java.source.version",
    ]:
        if prop_name in properties:
            ver = properties[prop_name]
            # Normalize: 1.8 → 8, 11 → 11
            if ver.startswith("1.") and len(ver) <= 4:
                ver = ver[2:]
            return ver
    # Check compiler plugin configuration
    for p in plugins:
        if p.artifact_id == "maven-compiler-plugin":
            for key in ["release", "source", "target"]:
                if key in p.configuration:
                    ver = p.configuration[key]
                    ver = resolve_property(ver, properties) or ver
                    if ver.startswith("1.") and len(ver) <= 4:
                        ver = ver[2:]
                    return ver
    return None


def detect_kotlin_version(properties: dict, plugins: list) -> Optional[str]:
    """Detect if the project uses Kotlin and extract its version.

    Checks for ``kotlin-maven-plugin`` in the plugins list first, then
    falls back to ``kotlin.version`` or ``kotlin-version`` properties.

    Args:
        properties: Merged properties dict.
        plugins: Combined plugins and pluginManagement entries.

    Returns:
        Kotlin version string (e.g. ``"2.2.10"``), or ``None`` if not a Kotlin project.
    """
    for p in plugins:
        if p.artifact_id == "kotlin-maven-plugin":
            if p.version:
                return resolve_property(p.version, properties) or p.version
    for prop in ["kotlin.version", "kotlin-version"]:
        if prop in properties:
            return properties[prop]
    return None


def is_spring_boot_project(module: MavenModule) -> bool:
    """Check whether the module is a Spring Boot project.

    Detection checks:
        1. Parent POM is ``spring-boot-starter-parent``
        2. ``spring-boot-maven-plugin`` is in plugins or pluginManagement

    Args:
        module: The root MavenModule to check.

    Returns:
        ``True`` if Spring Boot is detected.
    """
    return module.parent_artifact_id == "spring-boot-starter-parent" or any(
        p.artifact_id == "spring-boot-maven-plugin" for p in module.plugins + module.plugin_management
    )


def is_devtools(dep: Dependency) -> bool:
    """Check if a dependency is Spring Boot DevTools.

    DevTools should use the ``developmentOnly`` configuration in Gradle.

    Args:
        dep: The dependency to check.

    Returns:
        ``True`` if the artifactId is ``spring-boot-devtools``.
    """
    return dep.artifact_id == "spring-boot-devtools"
