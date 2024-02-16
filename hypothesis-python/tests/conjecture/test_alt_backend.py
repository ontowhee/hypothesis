# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Copyright the Hypothesis Authors.
# Individual contributors are listed in AUTHORS.rst and the git log.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.

import math
from contextlib import contextmanager
from random import Random
from typing import Optional, Sequence
import sys

import pytest

from hypothesis import given, settings, strategies as st
from hypothesis.database import InMemoryExampleDatabase
from hypothesis.internal.conjecture.data import AVAILABLE_PROVIDERS, ConjectureData
from hypothesis.internal.floats import SIGNALING_NAN
from hypothesis.internal.intervalsets import IntervalSet


class PrngProvider:
    # A test-only implementation of the PrimitiveProvider interface, which uses
    # a very simple PRNG to choose each value.  Dumb but efficient, and entirely
    # independent of our real backend

    def __init__(self, conjecturedata: "ConjectureData", /) -> None:
        self.prng = Random(conjecturedata.buffer or None)

    def draw_boolean(self, p: float = 0.5, *, forced: Optional[bool] = None) -> bool:
        if forced is not None:
            return forced
        return self.prng.random() < p

    def draw_integer(
        self,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
        *,
        # weights are for choosing an element index from a bounded range
        weights: Optional[Sequence[float]] = None,
        shrink_towards: int = 0,
        forced: Optional[int] = None,
    ) -> int:
        assert isinstance(shrink_towards, int)  # otherwise ignored here
        if forced is not None:
            return forced

        if weights is not None:
            assert min_value is not None
            assert max_value is not None
            return self.prng.choices(range(min_value, max_value + 1), weights=weights)

        if min_value is None and max_value is None:
            min_value = -(2**127)
            max_value = 2**127 - 1
        elif min_value is None:
            min_value = max_value - 2**64
        elif max_value is None:
            max_value = min_value + 2**64
        return self.prng.randint(min_value, max_value)

    def draw_float(
        self,
        *,
        min_value: float = -math.inf,
        max_value: float = math.inf,
        allow_nan: bool = True,
        smallest_nonzero_magnitude: float,
        forced: Optional[float] = None,
    ) -> float:
        if forced is not None:
            return forced

        if allow_nan and self.prng.random() < 1 / 32:
            nans = [math.nan, -math.nan, SIGNALING_NAN, -SIGNALING_NAN]
            value = self.prng.choice(nans)
            return value

        # small chance of inf values, if they are in bounds
        if min_value <= math.inf <= max_value and self.prng.random() < 1 / 32:
            return math.inf
        if min_value <= -math.inf <= max_value and self.prng.random() < 1 / 32:
            return -math.inf

        # get rid of infs, they cause nans if we pass them to prng.uniform
        if min_value in [-math.inf, math.inf]:
            min_value = math.copysign(1, min_value) * sys.float_info.max
            # being too close to the bounds causes prng.uniform to only return
            # inf.
            min_value /= 2
        if max_value in [-math.inf, math.inf]:
            max_value = math.copysign(1, max_value) * sys.float_info.max
            max_value /= 2

        value = self.prng.uniform(min_value, max_value)
        if value and abs(value) < smallest_nonzero_magnitude:
            return math.copysign(0.0, value)
        return value

    def draw_string(
        self,
        intervals: IntervalSet,
        *,
        min_size: int = 0,
        max_size: Optional[int] = None,
        forced: Optional[str] = None,
    ) -> str:
        if forced is not None:
            return forced
        size = self.prng.randint(
            min_size, max(min_size, min(100 if max_size is None else max_size, 100))
        )
        return "".join(map(chr, self.prng.choices(intervals, k=size)))

    def draw_bytes(self, size: int, *, forced: Optional[bytes] = None) -> bytes:
        if forced is not None:
            return forced
        return self.prng.randbytes(size)


@contextmanager
def temp_register_backend():
    try:
        AVAILABLE_PROVIDERS["prng"] = f"{__name__}.{PrngProvider.__name__}"
        yield
    finally:
        AVAILABLE_PROVIDERS.pop("prng")


@pytest.mark.parametrize(
    "strategy",
    [
        st.booleans(),
        st.integers(0, 10),
        st.floats(allow_nan=False),
        st.text(max_size=5),
        st.binary(),
    ],
    ids=repr,
)
def test_find_with_backend_then_convert_to_buffer_shrink_and_replay(strategy):
    db = InMemoryExampleDatabase()
    assert not db.data

    with temp_register_backend():

        @settings(database=db, backend="prng")
        @given(strategy)
        def test(value):
            if isinstance(value, float):
                # randomly generating 0 for floats is really unlikely
                assert value not in [math.inf, -math.inf]
            else:
                assert value

        with pytest.raises(AssertionError):
            test()

    assert db.data
    buffers = {x for x in db.data[next(iter(db.data))] if x}
    assert buffers, db.data
