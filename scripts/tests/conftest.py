"""Shared test fixtures for the Maven-to-Gradle migration test suite."""

import textwrap
from pathlib import Path

import pytest

from migrate.pom_models import Dependency, MavenModule, MavenProfile, Plugin


@pytest.fixture
def tmp_pom(tmp_path):
    """Factory fixture that writes a pom.xml to a temp directory and returns the path."""
    def _write(content: str) -> Path:
        pom = tmp_path / "pom.xml"
        pom.write_text(textwrap.dedent(content), encoding="utf-8")
        return pom
    return _write


@pytest.fixture
def simple_module():
    """A minimal single-module MavenModule for use in generator tests."""
    return MavenModule(
        group_id="com.example",
        artifact_id="demo",
        version="1.0.0",
        packaging="jar",
        properties={"java.version": "21"},
        dependencies=[
            Dependency(
                group_id="org.springframework.boot",
                artifact_id="spring-boot-starter-web",
            ),
            Dependency(
                group_id="org.junit.jupiter",
                artifact_id="junit-jupiter",
                scope="test",
            ),
        ],
    )


@pytest.fixture
def spring_boot_module():
    """A Spring Boot project MavenModule with parent POM."""
    return MavenModule(
        group_id="com.example",
        artifact_id="myapp",
        version="0.0.1-SNAPSHOT",
        packaging="jar",
        parent_artifact_id="spring-boot-starter-parent",
        parent_group_id="org.springframework.boot",
        parent_version="3.4.1",
        properties={
            "java.version": "21",
        },
        dependencies=[
            Dependency(
                group_id="org.springframework.boot",
                artifact_id="spring-boot-starter-web",
            ),
            Dependency(
                group_id="org.springframework.boot",
                artifact_id="spring-boot-devtools",
                scope="runtime",
                optional=True,
            ),
            Dependency(
                group_id="org.springframework.boot",
                artifact_id="spring-boot-starter-test",
                scope="test",
            ),
        ],
        plugins=[
            Plugin(
                group_id="org.springframework.boot",
                artifact_id="spring-boot-maven-plugin",
            ),
        ],
    )


@pytest.fixture
def multi_module_root():
    """A multi-module parent POM module."""
    return MavenModule(
        group_id="com.example",
        artifact_id="parent",
        version="1.0.0",
        packaging="pom",
        parent_artifact_id="spring-boot-starter-parent",
        parent_group_id="org.springframework.boot",
        parent_version="3.4.1",
        properties={"java.version": "21"},
        modules=["core", "web"],
        plugins=[
            Plugin(
                group_id="org.springframework.boot",
                artifact_id="spring-boot-maven-plugin",
            ),
        ],
    )


@pytest.fixture
def child_core_module():
    """A child 'core' module."""
    return MavenModule(
        group_id="com.example",
        artifact_id="core",
        version="1.0.0",
        packaging="jar",
        dependencies=[
            Dependency(
                group_id="com.fasterxml.jackson.core",
                artifact_id="jackson-databind",
            ),
        ],
        source_dir="core",
    )


@pytest.fixture
def child_web_module():
    """A child 'web' module that depends on 'core'."""
    return MavenModule(
        group_id="com.example",
        artifact_id="web",
        version="1.0.0",
        packaging="jar",
        dependencies=[
            Dependency(
                group_id="org.springframework.boot",
                artifact_id="spring-boot-starter-web",
            ),
            Dependency(
                group_id="com.example",
                artifact_id="core",
            ),
            Dependency(
                group_id="org.springframework.boot",
                artifact_id="spring-boot-starter-test",
                scope="test",
            ),
        ],
        source_dir="web",
    )
