import os
import re
from unittest import mock

import pytest
import pandas as pd
import numpy as np

from allensdk.brain_observatory.ecephys.ecephys_project_api import (
    ecephys_project_lims_api as epla,
)


class MockSelector:

    def __init__(self, checks, response):
        self.checks = checks
        self.response = response

    def __call__(self, query, *args, **kwargs):
        self.passed  = {}
        self.query = query
        for name, check in self.checks.items():
            self.passed[name] = check(query)
        return self.response


@pytest.mark.parametrize("method_name,kwargs,response,checks,expected", [
    [
        "get_units",
        {},
        pd.DataFrame({"id": [5, 6], "something": [12, 14]}),
        {
            "no_pa_check": lambda st: "published_at" not in st
        },
        pd.DataFrame(
            {"something": [12, 14]}, 
            index=pd.Index(name="id", data=[5, 6])
        )
    ],
    [
        "get_units",
        {"session_ids": [1, 2, 3]},
        pd.DataFrame({"id": [5, 6], "something": [12, 14]}),
        {
            "filters_sessions": lambda st: re.compile(r".+and es.id in \(1,2,3\).*", re.DOTALL).match(st) is not None
        },
        pd.DataFrame(
            {"something": [12, 14]}, 
            index=pd.Index(name="id", data=[5, 6])
        )
    ],
    [
        "get_units",
        {"unit_ids": [1, 2, 3]},
        pd.DataFrame({"id": [5, 6], "something": [12, 14]}),
        {
            "filters_units": lambda st: re.compile(r".+and eu.id in \(1,2,3\).*", re.DOTALL).match(st) is not None
        },
        pd.DataFrame(
            {"something": [12, 14]}, 
            index=pd.Index(name="id", data=[5, 6])
        )
    ],
    [
        "get_units",
        {"channel_ids": [1, 2, 3], "probe_ids": [4, 5, 6]},
        pd.DataFrame({"id": [5, 6], "something": [12, 14]}),
        {
            "filters_channels": lambda st: re.compile(r".+and ec.id in \(1,2,3\).*", re.DOTALL).match(st) is not None,
            "filters_probes": lambda st: re.compile(r".+and ep.id in \(4,5,6\).*", re.DOTALL).match(st) is not None
        },
        pd.DataFrame(
            {"something": [12, 14]}, 
            index=pd.Index(name="id", data=[5, 6])
        )
    ],
    [
        "get_units",
        {"published_at": "2019-10-22"},
        pd.DataFrame({"id": [5, 6], "something": [12, 14]}),
        {
            "checks_pa_not_null": lambda st: re.compile(r".+and es.published_at is not null.*", re.DOTALL).match(st) is not None,
            "checks_pa": lambda st: re.compile(r".+and es.published_at <= '2019-10-22'.*", re.DOTALL).match(st) is not None
        },
        pd.DataFrame(
            {"something": [12, 14]}, 
            index=pd.Index(name="id", data=[5, 6])
        )
    ],
    [
        "get_channels",
        {"published_at": "2019-10-22", "session_ids": [1, 2, 3]},
        pd.DataFrame({"id": [5, 6], "something": [12, 14]}),
        {
            "checks_pa_not_null": lambda st: re.compile(r".+and es.published_at is not null.*", re.DOTALL).match(st) is not None,
            "checks_pa": lambda st: re.compile(r".+and es.published_at <= '2019-10-22'.*", re.DOTALL).match(st) is not None,
            "filters_sessions": lambda st: re.compile(r".+and es.id in \(1,2,3\).*", re.DOTALL).match(st) is not None
        },
        pd.DataFrame(
            {"something": [12, 14]}, 
            index=pd.Index(name="id", data=[5, 6])
        )
    ],
    [
        "get_probes",
        {"published_at": "2019-10-22", "session_ids": [1, 2, 3]},
        pd.DataFrame({"id": [5, 6], "something": [12, 14]}),
        {
            "checks_pa_not_null": lambda st: re.compile(r".+and es.published_at is not null.*", re.DOTALL).match(st) is not None,
            "checks_pa": lambda st: re.compile(r".+and es.published_at <= '2019-10-22'.*", re.DOTALL).match(st) is not None,
            "filters_sessions": lambda st: re.compile(r".+and es.id in \(1,2,3\).*", re.DOTALL).match(st) is not None
        },
        pd.DataFrame(
            {"something": [12, 14]}, 
            index=pd.Index(name="id", data=[5, 6])
        )
    ],
    [
        "get_sessions",
        {"published_at": "2019-10-22", "session_ids": [1, 2, 3]},
        pd.DataFrame({"id": [5, 6], "something": [12, 14], "genotype": ["foo", np.nan]}),
        {
            "checks_pa_not_null": lambda st: re.compile(r".+and es.published_at is not null.*", re.DOTALL).match(st) is not None,
            "checks_pa": lambda st: re.compile(r".+and es.published_at <= '2019-10-22'.*", re.DOTALL).match(st) is not None,
            "filters_sessions": lambda st: re.compile(r".+and es.id in \(1,2,3\).*", re.DOTALL).match(st) is not None
        },
        pd.DataFrame(
            {"something": [12, 14], "genotype": ["foo", "wt"]}, 
            index=pd.Index(name="id", data=[5, 6])
        )
    ],
    [
        "get_unit_analysis_metrics",
        {"ecephys_session_ids": [1, 2, 3]},
        pd.DataFrame({"id": [5, 6], "data": [{"a": 1, "b": 2}, {"a": 3, "b": 4}], "ecephys_unit_id": [10, 11]}),
        {
            "filters_sessions": lambda st: re.compile(r".+and es.id in \(1,2,3\).*", re.DOTALL).match(st) is not None
        },
        pd.DataFrame(
            {"id": [5, 6], "a": [1, 3], "b": [2, 4]}, 
            index=pd.Index(name="iecephys_unit_id", data=[10, 11])
        )
    ]
])
def test_pg_query(method_name,kwargs, response, checks, expected):

    selector = MockSelector(checks, response)

    with mock.patch("allensdk.internal.api.psycopg2_select", new=selector) as ptc:
        api = epla.EcephysProjectLimsApi.default()
        obtained = getattr(api, method_name)(**kwargs)
        pd.testing.assert_frame_equal(expected, obtained, check_like=True, check_dtype=False)

        any_checks_failed = False
        for name, result in ptc.passed.items():
            if not result:
                print(f"check {name} failed")
                any_checks_failed = True
        
        if any_checks_failed:
            print(ptc.query)
        assert not any_checks_failed


def test_get_session_data():

    session_id = 12345
    wkf_id = 987

    class MockPgEngine:
        def select(self, rendered):
            pattern = re.compile(
                r".*and ear.ecephys_session_id = (?P<session_id>\d+).*", re.DOTALL
            )
            match = pattern.match(rendered)
            sid_obt = int(match["session_id"])
            assert session_id == sid_obt
            return pd.DataFrame({"id": [wkf_id]})

    class MockHttpEngine:
        def stream(self, path):
            assert path == f"well_known_files/download/{wkf_id}?wkf_id={wkf_id}"

    api = epla.EcephysProjectLimsApi(
        postgres_engine=MockPgEngine(), app_engine=MockHttpEngine()
    )
    api.get_session_data(session_id)


def test_get_probe_data():

    probe_id = 12345
    wkf_id = 987

    class MockPgEngine:
        def select(self, rendered):
            pattern = re.compile(
                r".*and earp.ecephys_probe_id = (?P<probe_id>\d+).*", re.DOTALL
            )
            match = pattern.match(rendered)
            pid_obt = int(match["probe_id"])
            assert probe_id == pid_obt
            return pd.DataFrame({"id": [wkf_id]})

    class MockHttpEngine:
        def stream(self, path):
            assert path == f"well_known_files/download/{wkf_id}?wkf_id={wkf_id}"

    api = epla.EcephysProjectLimsApi(
        postgres_engine=MockPgEngine(), app_engine=MockHttpEngine()
    )
    api.get_probe_lfp_data(probe_id)


@pytest.mark.parametrize("method,kwargs,query_pattern", [
    [
        "get_natural_movie_template",
        {"number": 12},
        re.compile(".+st.name = 'natural_movie_12'.+", re.DOTALL)
    ],
    [
        "get_natural_scene_template",
        {"number": 12},
        re.compile(".+st.name = 'natural_scene_12'.+", re.DOTALL)
    ]
])
def test_template_getter(method, kwargs, query_pattern):

    wkf_id = 12345

    class MockPgEngine:
        def select_one(self, rendered):
            assert query_pattern.match(rendered) is not None
            return {"well_known_file_id": wkf_id}

    class MockHttpEngine:
        def stream(self, url):
             assert url == f"well_known_files/download/{wkf_id}?wkf_id={wkf_id}"

    api = epla.EcephysProjectLimsApi(
        postgres_engine=MockPgEngine(), app_engine=MockHttpEngine()
    )
    getattr(api, method)(**kwargs)