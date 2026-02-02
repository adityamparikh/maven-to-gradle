"""Tests for maven_gradle_mappings.py — alias generation and scope mapping."""

from migrate.maven_gradle_mappings import (
    to_alias, to_version_key, to_plugin_alias, gradle_config,
    SCOPE_MAP, PLUGIN_ID_MAP, PLUGIN_SKIP,
)


class TestToAlias:
    # Spring ecosystem
    def test_spring_boot_starter(self):
        assert to_alias("org.springframework.boot", "spring-boot-starter-web") == "spring-boot-starter-web"

    def test_spring_boot_no_stutter(self):
        assert to_alias("org.springframework.boot", "spring-boot-starter-data-jpa") == "spring-boot-starter-data-jpa"

    def test_spring_cloud(self):
        assert to_alias("org.springframework.cloud", "spring-cloud-starter-netflix-eureka-client") == "spring-cloud-starter-netflix-eureka-client"

    def test_spring_security(self):
        assert to_alias("org.springframework.security", "spring-security-test") == "spring-security-test"

    # Jackson
    def test_jackson_core(self):
        assert to_alias("com.fasterxml.jackson.core", "jackson-databind") == "jackson-databind"

    def test_jackson_module(self):
        assert to_alias("com.fasterxml.jackson.module", "jackson-module-kotlin") == "jackson-module-kotlin"

    # Testing libraries
    def test_junit_jupiter(self):
        assert to_alias("org.junit.jupiter", "junit-jupiter") == "junit-jupiter"

    def test_mockito(self):
        assert to_alias("org.mockito", "mockito-core") == "mockito-core"

    def test_testcontainers(self):
        assert to_alias("org.testcontainers", "testcontainers") == "testcontainers"

    # Exact match (prefix == artifact)
    def test_lombok(self):
        assert to_alias("org.projectlombok", "lombok") == "lombok"

    def test_h2(self):
        assert to_alias("com.h2database", "h2") == "h2"

    # Fallback: groupId last segment + artifactId
    def test_unknown_group(self):
        assert to_alias("com.acme.internal", "my-lib") == "internal-my-lib"

    def test_unknown_group_no_stutter(self):
        alias = to_alias("com.acme.foobar", "foobar-utils")
        assert alias == "foobar-utils"

    # AWS
    def test_aws_sdk(self):
        assert to_alias("software.amazon.awssdk", "s3") == "aws-s3"

    # Sanitization
    def test_special_chars_sanitized(self):
        alias = to_alias("com.example", "my_lib.v2")
        assert "." not in alias
        assert "_" not in alias


class TestToVersionKey:
    def test_simple(self):
        assert to_version_key("spring-boot") == "spring-boot"

    def test_special_chars(self):
        assert to_version_key("my.lib_v2") == "my-lib-v2"

    def test_leading_trailing_hyphens(self):
        assert to_version_key("-foo-") == "foo"


class TestToPluginAlias:
    def test_strips_maven_plugin_suffix(self):
        alias = to_plugin_alias("org.apache.maven.plugins", "jacoco-maven-plugin")
        assert "maven-plugin" not in alias

    def test_strips_gradle_plugin_suffix(self):
        alias = to_plugin_alias("com.example", "my-tool-gradle-plugin")
        assert "gradle-plugin" not in alias

    def test_strips_plain_plugin_suffix(self):
        alias = to_plugin_alias("com.example", "something-plugin")
        assert alias.endswith("something") or "plugin" not in alias


class TestGradleConfig:
    def test_compile(self):
        assert gradle_config("compile") == "implementation"

    def test_provided(self):
        assert gradle_config("provided") == "compileOnly"

    def test_runtime(self):
        assert gradle_config("runtime") == "runtimeOnly"

    def test_test(self):
        assert gradle_config("test") == "testImplementation"

    def test_system(self):
        assert gradle_config("system") == "compileOnly"

    def test_import(self):
        assert gradle_config("import") == "platform"

    def test_unknown_defaults_to_implementation(self):
        assert gradle_config("weird") == "implementation"


class TestConstantIntegrity:
    def test_scope_map_covers_standard_scopes(self):
        for scope in ["compile", "provided", "runtime", "test", "system", "import"]:
            assert scope in SCOPE_MAP

    def test_plugin_id_map_and_skip_are_disjoint(self):
        overlap = set(PLUGIN_ID_MAP.keys()) & PLUGIN_SKIP
        assert overlap == set(), f"Plugins in both maps: {overlap}"
