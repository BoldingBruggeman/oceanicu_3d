"""Tests that check_inputs.py lists the files the driver hooks actually read.

check_inputs.py keeps its own copies of the hooks' path logic (pygetm-config
is not installed where it runs), so these tests pin the two together.

Run with:  python3 -m pytest test_check_inputs.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "driver" / "scripts"
sys.path.insert(0, str(HERE))
import check_inputs as ci  # noqa: E402


# File-name and variable literals that must appear in both a hook and check_inputs.py.
SHARED_LITERALS = {
    "fabm.py": ["_15deg", "AMM7_Ndep_BC-EMEP_historical_y????.nc",
                "AMM7-EMEP-NDeposition_y????.nc", '"N3_flux_ff"', '"N4_flux_ff"',
                "mole_fraction_of_carbon_dioxide_in_air", "mole_fraction_of_nitrous_oxide_in_air",
                "ghg_scenario", "ADY_gle.nc", "fishing_effort_????_AMM7.nc"],
    "rivers.py": ["${RIVER_FOLDER}", '"RIVER_FILE"', "EMORID_1993_2024_conc_nemo_TALK_DIC.nc",
                  "EMORID_nutrient_climatology_monthly_", "nutrient_climatology", "_daily.nc"],
    "hydrography.py": ["woa_s.nc", "woa_t.nc", "so_2025_monthly_ic.nc", "thetao_2025_monthly_ic.nc",
                       '"so_ff"', '"thetao_ff"', "ic_folder", "ic_file", "ic_variable", "restart_file"],
}


@pytest.mark.parametrize("hook, literal", [(h, lit) for h, lits in SHARED_LITERALS.items() for lit in lits])
def test_literal_shared_with_hook(hook, literal):
    assert literal in (SCRIPTS / hook).read_text(), f"{literal!r} no longer in {hook} -- update this test"
    assert literal in (HERE / "check_inputs.py").read_text(), f"check_inputs.py lacks {literal!r} from {hook}"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    for var in ("GHG_CONCENTRATION_FOLDER", "RIVER_FOLDER", "FABM_FOLDER", "RIVER_FOLDER_CMIP6", "BFC"):
        d = tmp_path / var
        d.mkdir()
        monkeypatch.setenv(var, str(d))
    monkeypatch.delenv("RIVER_FILE", raising=False)
    return tmp_path


def _paths(checks):
    return [Path(c.path).name for c in checks]


def test_fabm_ghg_and_ndep_follow_ghg_scenario(roots):
    config = {"fabm": {"source": "ERSEM", "folder": "${FABM_FOLDER}", "ghg_scenario": "ssp245"},
              "runtime": {"calendar": "noleap"}}
    names = _paths(ci._list_fabm_files(config, "2014-01-01", "2016-01-01", None))
    assert "co2_historical_15deg_noleap.nc" in names
    assert "co2_ssp245_15deg_noleap.nc" in names
    assert "n2o_ssp245_15deg_noleap.nc" in names
    assert "co2_ssp245.nc" not in names
    assert any("AMM7_Ndep_BC-EMEP_" in n for n in names)  # pattern reported, no files on disk
    assert not any("AMM7-EMEP-NDeposition" in n for n in names)


def test_fabm_without_scenario_past_2014_fails(roots):
    config = {"fabm": {"source": "ERSEM", "folder": "${FABM_FOLDER}"}}
    checks = ci._list_fabm_files(config, "2010-01-01", "2020-01-01", None)
    assert any(c.description == "FABM GHG/N-deposition scenario" and not c.ok for c in checks)


def test_fabm_tracer_ic_keys(roots):
    tracers = {"N3_n": {"file": "bdy.nc", "variable": "no3", "ic_file": "woa_n.nc", "ic_variable": "n_an"},
               "P1_c": {"constant": 0.0001}, "Z4_c": {"boundary_condition_type": "ZERO_GRADIENT"}}
    config = {"fabm": {"source": "ERSEM", "folder": "${FABM_FOLDER}", "ghg_scenario": "ssp245"},
              "boundaries": {"fabm": {"source": "CMEMS", "folder": "${BFC}", "ic_folder": "${BFC}",
                                      "tracers": tracers}}}
    checks = ci._list_fabm_files(config, "2015-02-01", "2016-01-01", None)
    ic = [c for c in checks if c.description.startswith("FABM tracer IC")]
    assert [(c.description, Path(c.path).name) for c in ic] == [("FABM tracer IC N3_n", "woa_n.nc")]
    assert not [c for c in ci._list_fabm_files(config, "2015-02-01", "2016-01-01", None, fresh_start=False)
                if "IC" in c.description]
    config["fabm"]["restart_file"] = "${BFC}/restart_ersem.nc"
    ic = [c for c in ci._list_fabm_files(config, "2015-02-01", "2016-01-01", None) if "IC" in c.description]
    assert [c.description for c in ic] == ["FABM restart IC"]


def test_rivers_cmip6_lists_record_projection_and_climatology(roots):
    config = {"river_discharge": {"source": "CMIP6", "folder": "${RIVER_FOLDER_CMIP6}",
                                  "file": "rivers_{scenario}.nc", "scenario": "ssp126"}}
    files = ci._list_river_files(config, "2030-01-01", "2031-01-01")
    assert [(Path(f[1]).name, f[4]) for f in files] == [
        ("EMORID_1993_2024_conc_nemo_TALK_DIC.nc", True),
        ("rivers_ssp126.nc", True),
        ("EMORID_nutrient_climatology_monthly_5yr.nc", False),
    ]


def test_rivers_emorid_reads_data_from_river_folder(roots):
    config = {"river_discharge": {"source": "emorid", "folder": "${RIVER_FOLDER_CMIP6}", "file": "pos.nc"}}
    files = ci._list_river_files(config, "2010-01-01", "2011-01-01")
    assert [(f[0], Path(f[1]).name) for f in files] == [
        ("river data (EMORID)", "EMORID_1993_2024_conc_nemo_TALK_DIC.nc"), ("river positions", "pos.nc")]


def test_expand_year_glob_january_buffer(tmp_path):
    for y in (2009, 2010, 2011):
        (tmp_path / f"f_{y}.nc").write_text("")
    got = ci._expand_year_glob(str(tmp_path / "f_????.nc"), "2010-01-01", "2010-06-01")
    assert [Path(p).name for p in got] == ["f_2009.nc", "f_2010.nc"]
