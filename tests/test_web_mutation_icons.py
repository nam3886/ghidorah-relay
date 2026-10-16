"""Mutation markers of the local icon components (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SHAPES = "apps/admin-web/src/components/shared/icons/icon-shapes.ts"
DEFINE = "apps/admin-web/src/components/shared/icons/define-icon.ts"
SPEC = "tests/unit/shared/icons.spec.ts"


@mutation(SHAPES, 'moon: { nodes: [p("M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z")] }',
          'moon: { nodes: [p("M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.9z")] }', expect="has exactly the canvas icons")
@mutation(SHAPES, 'play: { nodes: [p("M8 5.5v13l11-6.5z")], fill: "solid" }',
          'play: { nodes: [p("M8 5.5v13l11-6.5z")] }', expect="fills the play triangles and nothing else")
@mutation(DEFINE, 'stroke: "currentColor",', 'stroke: "#000000",', expect="draws on the 24 box")
@mutation(DEFINE, '"stroke-linecap": "round",', '"stroke-linecap": "butt",', expect="draws on the 24 box")
@mutation(DEFINE, "stroke: { type: Number as PropType<number>, default: 1.8 },",
          "stroke: { type: Number as PropType<number>, default: 2 },", expect="draws on the 24 box")
@mutation(DEFINE, "width: props.size,", "width: 16,", expect="takes its size and stroke from the props")
@pytest.mark.web_unit
def test_the_icons_keep_the_canvas_shapes_and_stroke():
    vitest(SPEC)
