"""Mutation markers for the placeholder guard: a marker left in any file of apps/admin-web/src must turn it red."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"


@mutation(f"{SRC}/App.vue", '<script setup lang="ts">', '<script setup lang="ts">\n// relay-admin-placeholder',
          expect="FAILED tests/unit/placeholders.spec.ts > placeholder marker > is in no file of src/")
@mutation(f"{SRC}/main.ts", "const app = createApp(App);", "const app = createApp(App); // relay-admin-placeholder",
          expect="FAILED tests/unit/placeholders.spec.ts > placeholder marker > is in no file of src/")
@pytest.mark.web_unit
def test_a_placeholder_marker_left_in_src_fails_the_guard():
    vitest("tests/unit/placeholders.spec.ts")
