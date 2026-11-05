from __future__ import annotations

from engine.core import Detection
from engine.tracking.bytetrack import ByteTrackTracker
from tests.conftest import fake_frame

FPS = 30.0


def make_tracker() -> ByteTrackTracker:
    return ByteTrackTracker(confirm_hits=3, confirm_window=5, lost_timeout_s=2.0, fps=FPS)


def det(x: float, y: float = 100.0) -> Detection:
    return Detection(box=(x, y, x + 40.0, y + 30.0), score=0.9, class_name="truck")


def step(tracker, i: int, dets):
    return tracker.update(dets, fake_frame(i, i / FPS))


def test_confirmed_after_three_hits():
    t = make_tracker()
    assert step(t, 0, [det(10)]).confirmed == []
    assert step(t, 1, [det(12)]).confirmed == []
    res = step(t, 2, [det(14)])
    assert len(res.confirmed) == 1
    assert res.confirmed[0].detection.class_name == "truck"


def test_one_frame_blip_never_confirmed():
    t = make_tracker()
    assert step(t, 0, [det(10)]).confirmed == []
    for i in range(1, 11):
        res = step(t, i, [])
        assert res.confirmed == []
        assert res.ended_ids == []


def test_three_of_five_with_gaps():
    t = make_tracker()
    results = []
    for i in range(5):
        results.append(step(t, i, [det(10 + 2 * i)] if i % 2 == 0 else []))
    assert [len(r.confirmed) for r in results] == [0, 0, 0, 0, 1]


def test_short_gap_keeps_id():
    t = make_tracker()
    box = det(100)
    ids = set()
    ended: list[int] = []
    for i in range(0, 30):
        r = step(t, i, [box])
        ids.update(tr.track_id for tr in r.confirmed)
        ended += r.ended_ids
    assert len(ids) == 1
    for i in range(30, 75):
        ended += step(t, i, []).ended_ids
    after = set()
    for i in range(75, 91):
        r = step(t, i, [box])
        after.update(tr.track_id for tr in r.confirmed)
        ended += r.ended_ids
    assert after == ids
    assert ended == []


def test_long_gap_ends_track():
    t = make_tracker()
    box = det(100)
    ids = set()
    ended: list[int] = []
    for i in range(0, 30):
        r = step(t, i, [box])
        ids.update(tr.track_id for tr in r.confirmed)
    for i in range(30, 30 + 63):  # 2.1 s hidden
        ended += step(t, i, []).ended_ids
    assert len(ids) == 1
    assert ended == list(ids)


def test_two_trucks_two_ids():
    t = make_tracker()
    res = None
    for i in range(4):
        res = step(t, i, [det(10 + 2 * i, 20), det(200 + 2 * i, 180)])
    assert len({tr.track_id for tr in res.confirmed}) == 2


def test_low_score_detection_confirmed():
    t = make_tracker()
    low = Detection(box=(10.0, 100.0, 50.0, 130.0), score=0.5, class_name="truck")
    results = [step(t, i, [low]) for i in range(3)]
    assert [len(r.confirmed) for r in results] == [0, 0, 1]


def test_ended_id_never_confirmed_again_at_29_97_fps():
    fps = 29.97
    for hidden in range(58, 66):
        t = ByteTrackTracker(confirm_hits=3, confirm_window=5, lost_timeout_s=2.0, fps=fps)
        box = det(100)
        ids: set[int] = set()
        ended: set[int] = set()
        i = 0
        for _ in range(30):
            ids.update(tr.track_id for tr in t.update([box], fake_frame(i, i / fps)).confirmed)
            i += 1
        for _ in range(hidden):
            ended.update(t.update([], fake_frame(i, i / fps)).ended_ids)
            i += 1
        for _ in range(20):
            r = t.update([box], fake_frame(i, i / fps))
            assert not ({tr.track_id for tr in r.confirmed} & ended), hidden
            ended.update(r.ended_ids)
            i += 1


def test_irregular_frame_skips_keep_one_id():
    # Realtime mode drops frames at a varying rate (inference 4.5-30 FPS): a truck
    # moving at constant speed must keep one id however many frames were skipped.
    t = make_tracker()
    pattern = [1] * 6 + [3, 5, 7, 4, 6, 3, 7, 5]  # full rate, then every 3rd-7th frame
    ids: set[int] = set()
    ended: list[int] = []
    i = k = 0
    while i < 600:
        x = 10.0 + 5.0 * i  # 150 px/s, box 40 px wide
        r = step(t, i, [det(x)])
        ids.update(tr.track_id for tr in r.confirmed)
        ended += r.ended_ids
        i += pattern[k % len(pattern)]
        k += 1
    assert len(ids) == 1
    assert ended == []


def test_library_lost_buffer_outlasts_our_timeout():
    # Our lost_timeout_s rule must be the only thing that ends a track.
    t = make_tracker()
    assert t._tracker.maximum_time_without_update >= 2.0 + 1.0
