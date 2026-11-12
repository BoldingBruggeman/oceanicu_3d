"""Tests for the vertical remap set_hydrography_ic uses to seed FABM from a restart.

_conservative_remap_column is nested inside the hook (hooks must stay
self-contained for codegen), so it is pulled out of the source with ast.

Run with:  python3 -m pytest driver/test_hydrography_remap.py -v
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

_SRC = Path(__file__).resolve().parent / "scripts" / "hydrography.py"


def _load_remap():
    tree = ast.parse(_SRC.read_text())
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_conservative_remap_column")
    ns = {"np": np}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(_SRC), "exec"), ns)
    return ns["_conservative_remap_column"]


remap = _load_remap()


def _ifaces(top, depth, n):
    return np.linspace(top, -depth, n + 1)  # decreasing, surface first


@pytest.mark.parametrize("src_top, tgt_top", [(-0.5, 0.0), (0.0, -0.5), (0.3, 0.3)])
def test_uniform_tracer_stays_uniform(src_top, tgt_top):
    """A target column higher or lower than the source (another tidal
    elevation) must not get zeros in its top or bottom layers."""
    src = _ifaces(src_top, 5.0, 20)
    tgt = _ifaces(tgt_top, 5.5, 30)
    vals = np.full((20, 2), 10.0)
    out = remap(vals, src, tgt)
    assert out.shape == (30, 2)
    np.testing.assert_allclose(out, 10.0)


def test_extends_nearest_layer_outside_source_range():
    src = _ifaces(0.0, 4.0, 4)  # layers 0-1, 1-2, 2-3, 3-4 m
    vals = np.array([[1.0], [2.0], [3.0], [4.0]])
    tgt = np.array([1.0, 0.0, -2.0, -4.0, -5.0])  # above, two inside, below
    out = remap(vals, src, tgt)[:, 0]
    np.testing.assert_allclose(out, [1.0, 1.5, 3.5, 4.0])


def test_conserves_content_inside_source_range():
    rng = np.random.default_rng(0)
    src = _ifaces(0.0, 10.0, 17)
    vals = rng.uniform(0, 5, (17, 3))
    tgt = _ifaces(0.0, 10.0, 23)
    out = remap(vals, src, tgt)
    src_content = (vals * (src[:-1] - src[1:])[:, None]).sum(axis=0)
    tgt_content = (out * (tgt[:-1] - tgt[1:])[:, None]).sum(axis=0)
    np.testing.assert_allclose(tgt_content, src_content)
