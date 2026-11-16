from engine.evaluation.synthetic import StreamSpec, make_stream


def _flat(cache):
    return [(f.index, d) for f in cache.frames for d in f.detections]


def _shape(cache, ident):
    return [
        (f.index, tuple(d.box), d.score, d.class_name, ident.get(id(d)))
        for f in cache.frames
        for d in f.detections
    ]


def test_stream_is_deterministic():
    spec = StreamSpec(junk_per_s=2.0, seconds=3.0)
    a, ia = make_stream(spec, seed=5)
    b, ib = make_stream(spec, seed=5)
    assert _shape(a, ia) == _shape(b, ib)
    c, ic = make_stream(spec, seed=6)
    assert _shape(a, ia) != _shape(c, ic)
    assert a.video == "synthetic" and a.classes == ("car", "truck", "bus")
    assert len(a.frames) == 90


def test_identities_and_classes():
    cache, ident = make_stream(StreamSpec(seconds=1.0))
    seen = {}
    for _, d in _flat(cache):
        seen.setdefault(ident[id(d)], set()).add(d.class_name)
    assert seen == {0: {"truck"}, 1: {"truck"}, 2: {"truck"}, 3: {"car"}, 4: {"car"}}


def test_low_score_run_sets_scores():
    spec = StreamSpec(seconds=4.0, low_score_runs=[(0, 1.0, 2.0, 0.2)])
    cache, ident = make_stream(spec)
    for f in cache.frames:
        for d in f.detections:
            if ident[id(d)] == 0:
                want = 0.2 if 1.0 <= f.ts < 2.0 else 0.7
                assert d.score == want


def test_class_flip_sets_class():
    spec = StreamSpec(seconds=4.0, class_flips=[(1, 1.0, 2.0, "car")])
    cache, ident = make_stream(spec)
    for f in cache.frames:
        for d in f.detections:
            if ident[id(d)] == 1:
                want = "car" if 1.0 <= f.ts < 2.0 else "truck"
                assert d.class_name == want


def test_dropout_removes_detections():
    spec = StreamSpec(seconds=4.0, dropouts=[(2, 1.0, 2.0)])
    cache, ident = make_stream(spec)
    for f in cache.frames:
        ids = {ident[id(d)] for d in f.detections}
        assert (2 in ids) == (not (1.0 <= f.ts < 2.0))


def test_pan_shifts_all_boxes():
    base, bi = make_stream(StreamSpec(seconds=1.0, junk_per_s=5.0), seed=1)
    pan, pi = make_stream(StreamSpec(seconds=1.0, junk_per_s=5.0, pan_px_s=100.0), seed=1)
    checked = 0
    for fb, fp in zip(base.frames, pan.frames):
        # same truth objects, shifted by pan * t (clipping may drop some)
        bm = {bi[id(d)]: d for d in fb.detections if id(d) in bi}
        for d in fp.detections:
            if id(d) in pi and pi[id(d)] in bm:
                dx = d.box[0] - bm[pi[id(d)]].box[0]
                assert abs(dx - 100.0 * fp.ts) < 1e-6
                checked += 1
    assert checked > 0
    # junk is shifted too: junk boxes exist in both streams
    assert any(id(d) not in pi for f in pan.frames for d in f.detections)


def test_junk_has_no_identity():
    cache, ident = make_stream(StreamSpec(seconds=3.0, junk_per_s=5.0))
    junk = [d for _, d in _flat(cache) if id(d) not in ident]
    assert junk
    for d in junk:
        assert d.class_name == "truck"
        assert 0.15 <= d.score <= 0.35


def test_objects_never_overlap_and_stay_in_frame():
    cache, ident = make_stream(StreamSpec(seconds=10.0))
    for f in cache.frames:
        boxes = [d.box for d in f.detections]
        for d in f.detections:
            x1, y1, x2, y2 = d.box
            assert 0 <= x1 < x2 <= cache.width and 0 <= y1 < y2 <= cache.height
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
