"""Maven-to-Gradle translation tables and alias generation.

Pure mapping logic with no XML parsing, no file I/O, and no internal
package imports. All functions are stateless string transformations.
"""

import re

# Maven scope → Gradle configuration mapping.
# Key difference: Maven's "compile" is transitive; Gradle's "implementation" is NOT.
# Use the `java-library` plugin and `api` configuration when transitivity is needed.
SCOPE_MAP = {
    "compile": "implementation",
    "provided": "compileOnly",
    "runtime": "runtimeOnly",
    "test": "testImplementation",
    "system": "compileOnly",
    "import": "platform",
}

# Known Maven plugin → Gradle plugin ID mapping.
# Plugins not in this map and not in PLUGIN_SKIP are silently skipped with no
# catalog entry. Add new mappings here when supporting additional plugins.
PLUGIN_ID_MAP = {
    "spring-boot-maven-plugin": "org.springframework.boot",
    "kotlin-maven-plugin": "org.jetbrains.kotlin.jvm",
    "kotlin-allopen": "org.jetbrains.kotlin.plugin.allopen",
    "kotlin-noarg": "org.jetbrains.kotlin.plugin.noarg",
    "jib-maven-plugin": "com.google.cloud.tools.jib",
    "jacoco-maven-plugin": "jacoco",
    "maven-checkstyle-plugin": "checkstyle",
    "maven-pmd-plugin": "pmd",
    "spotbugs-maven-plugin": "com.github.spotbugs",
    "spotless-maven-plugin": "com.diffplug.spotless",
    "maven-shade-plugin": "com.github.johnrengelman.shadow",
    "maven-war-plugin": "war",
    "maven-ear-plugin": "ear",
    "maven-application-plugin": "application",
    "asciidoctor-maven-plugin": "org.asciidoctor.jvm.convert",
    "flyway-maven-plugin": "org.flywaydb.flyway",
    "jooq-codegen-maven": "nu.studer.jooq",
    "openapi-generator-maven-plugin": "org.openapi.generator",
    "protobuf-maven-plugin": "com.google.protobuf",
    "git-commit-id-plugin": "com.gorylenko.gradle-git-properties",
}

# Maven plugins that have no direct Gradle plugin equivalent.
# These are handled via built-in Gradle tasks, conventions, or are unnecessary.
# Each entry documents the Gradle alternative as an inline comment.
PLUGIN_SKIP = {
    "maven-compiler-plugin",        # → java toolchain / kotlin options
    "maven-surefire-plugin",        # → test task config
    "maven-failsafe-plugin",        # → custom integration test task
    "maven-resources-plugin",       # → processResources task
    "maven-jar-plugin",             # → jar task config
    "maven-source-plugin",          # → java { withSourcesJar() }
    "maven-javadoc-plugin",         # → java { withJavadocJar() }
    "maven-deploy-plugin",          # → maven-publish plugin
    "maven-install-plugin",         # → not needed
    "maven-clean-plugin",           # → built-in
    "maven-site-plugin",            # → not needed
    "maven-project-info-reports-plugin",  # → not needed
    "maven-dependency-plugin",      # → dependencies task / configuration
    "maven-enforcer-plugin",        # → see references/gotchas.md
    "maven-release-plugin",         # → see references/gotchas.md
    "versions-maven-plugin",        # → version catalog + dependabot
    "flatten-maven-plugin",         # → not needed in Gradle
    "maven-antrun-plugin",          # → ant integration in Gradle
}


def to_alias(group_id: str, artifact_id: str) -> str:
    """Generate a Gradle version catalog alias from Maven GAV coordinates.

    Uses a ``prefix_map`` to collapse common groupId prefixes into short,
    idiomatic catalog aliases. For example:

        org.springframework.boot : spring-boot-starter-web → spring-boot-starter-web
        org.testcontainers       : testcontainers-postgresql → testcontainers-postgresql
        io.awspring.cloud        : spring-cloud-aws-starter-s3 → spring-cloud-aws-starter-s3

    The prefix_map is ordered with more-specific prefixes before less-specific
    ones (e.g. ``io.micronaut.data`` before ``io.micronaut``) because matching
    uses ``str.startswith()`` with first-match-wins semantics.

    Anti-stutter logic prevents aliases like ``spring-boot-spring-boot-starter-web``
    by detecting when the artifact_id already starts with the prefix words.

    Args:
        group_id: Maven groupId.
        artifact_id: Maven artifactId.

    Returns:
        A sanitized kebab-case alias string suitable for ``libs.versions.toml``.
    """
    # Common group prefixes to strip for cleaner aliases.
    # ORDERING MATTERS: more-specific prefixes must come before less-specific
    # ones because matching uses startswith() with first-match-wins.
    prefix_map = {
        # Spring ecosystem
        "org.springframework.boot": "spring-boot",
        "org.springframework.cloud": "spring-cloud",
        "org.springframework.data": "spring-data",
        "org.springframework.security": "spring-security",
        "org.springframework.kafka": "spring-kafka",
        "org.springframework": "spring",
        "io.awspring.cloud": "spring-cloud-aws",
        # Apache
        "org.apache.commons": "commons",
        "org.apache.kafka": "kafka",
        "org.apache.solr": "solr",
        "org.apache.lucene": "lucene",
        "org.apache.httpcomponents": "httpcomponents",
        "org.apache.logging.log4j": "log4j",
        # Jackson
        "com.fasterxml.jackson.core": "jackson",
        "com.fasterxml.jackson.module": "jackson-module",
        "com.fasterxml.jackson.datatype": "jackson-datatype",
        "com.fasterxml.jackson.dataformat": "jackson-dataformat",
        # Reactive / observability
        "io.projectreactor": "reactor",
        "io.micrometer": "micrometer",
        # Quarkus (more-specific before less-specific)
        "io.quarkus.platform": "quarkus-platform",
        "io.quarkus": "quarkus",
        # Micronaut (more-specific before less-specific)
        "io.micronaut.data": "micronaut-data",
        "io.micronaut.sql": "micronaut-sql",
        "io.micronaut.serde": "micronaut-serde",
        "io.micronaut.test": "micronaut-test",
        "io.micronaut.testresources": "micronaut-testresources",
        "io.micronaut.flyway": "micronaut-flyway",
        "io.micronaut.validation": "micronaut-validation",
        "io.micronaut": "micronaut",
        # Networking / gRPC
        "io.grpc": "grpc",
        "io.netty": "netty",
        # Resilience
        "io.github.resilience4j": "resilience4j",
        # Testing
        "org.junit.jupiter": "junit-jupiter",
        "org.mockito": "mockito",
        "org.assertj": "assertj",
        "org.testcontainers": "testcontainers",
        "org.mock-server": "mockserver",
        "org.wiremock": "wiremock",
        "com.github.tomakehurst": "wiremock",
        "org.awaitility": "awaitility",
        # Logging
        "ch.qos.logback": "logback",
        "org.slf4j": "slf4j",
        # ORM / database
        "org.hibernate.orm": "hibernate",
        "org.hibernate.validator": "hibernate-validator",
        "org.mongodb": "mongodb",
        "org.postgresql": "postgresql",
        "com.h2database": "h2",
        "com.mysql": "mysql",
        "org.flywaydb": "flyway",
        "org.liquibase": "liquibase",
        "redis.clients": "redis",
        # AWS
        "software.amazon.awssdk": "aws",
        "com.amazonaws": "aws-classic",
        # Build / annotation processing
        "org.projectlombok": "lombok",
        "org.mapstruct": "mapstruct",
        "com.google.guava": "guava",
        "com.google.cloud.tools": "google-cloud-tools",
        # Jakarta / Javax
        "jakarta.": "jakarta",
        "javax.": "javax",
    }

    alias_prefix = None
    for maven_prefix, catalog_prefix in prefix_map.items():
        if group_id.startswith(maven_prefix):
            alias_prefix = catalog_prefix
            break

    if alias_prefix:
        # Check if prefix alone is the artifact (e.g., "lombok" for lombok, "h2" for h2)
        if alias_prefix == artifact_id or (
            alias_prefix.replace("-", "") == artifact_id.replace("-", "")
        ):
            alias = alias_prefix
        else:
            # If artifact_id already starts with prefix content, avoid stuttering
            # e.g., spring-boot + spring-boot-starter-web → spring-boot-starter-web
            prefix_words = alias_prefix.replace("-", " ").split()
            artifact_words = artifact_id.replace("-", " ").split()
            # Check overlap
            overlap = 0
            for i, pw in enumerate(prefix_words):
                if i < len(artifact_words) and artifact_words[i] == pw:
                    overlap = i + 1
                else:
                    break
            if overlap > 0:
                alias = artifact_id
            else:
                alias = f"{alias_prefix}-{artifact_id}"
    else:
        # Use groupId last segment + artifactId
        group_last = group_id.split(".")[-1]
        if artifact_id.startswith(group_last):
            alias = artifact_id
        else:
            alias = f"{group_last}-{artifact_id}"

    # Sanitize: Gradle catalog aliases use kebab-case (hyphens, dots, or underscores)
    alias = re.sub(r"[^a-zA-Z0-9\-]", "-", alias)
    alias = re.sub(r"-+", "-", alias).strip("-").lower()
    return alias


def to_version_key(name: str) -> str:
    """Sanitize a name into a kebab-case version reference key.

    Replaces non-alphanumeric characters (except hyphens) with hyphens,
    collapses runs of hyphens, and lowercases.

    Args:
        name: Raw name string to sanitize.

    Returns:
        A clean kebab-case key suitable for the ``[versions]`` section.
    """
    return re.sub(r"[^a-zA-Z0-9\-]", "-", name).strip("-").lower()


def to_plugin_alias(group_id: str, artifact_id: str) -> str:
    """Generate a plugin alias for the version catalog ``[plugins]`` section.

    Strips common Maven/Gradle plugin suffixes (``-maven-plugin``,
    ``-gradle-plugin``, ``-plugin``) before delegating to ``to_alias()``.
    Longer suffixes are checked first to avoid partial matches.

    Args:
        group_id: Plugin groupId.
        artifact_id: Plugin artifactId.

    Returns:
        A sanitized plugin alias string.
    """
    name = artifact_id
    for suffix in ["-gradle-plugin", "-maven-plugin", "-plugin"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return to_alias(group_id, name)


def gradle_config(scope: str) -> str:
    """Map a Maven dependency scope to the corresponding Gradle configuration.

    Args:
        scope: Maven scope string (compile, provided, runtime, test, system, import).

    Returns:
        Gradle configuration name (e.g. ``"implementation"``, ``"testImplementation"``).
        Defaults to ``"implementation"`` for unknown scopes.
    """
    return SCOPE_MAP.get(scope, "implementation")
