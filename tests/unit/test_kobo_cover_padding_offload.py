"""Behavioral regression for Kobo cover padding request isolation."""

import threading

from cps.services import cover_preview, parallel


def _run_on_real_thread(job):
    result = []
    failure = []

    def worker():
        try:
            result.append(job())
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0]


def test_cover_cache_miss_offloads_padding_but_hit_stays_on_caller(monkeypatch, tmp_path):
    """Wand work hops only on a miss; path/cache work remains caller-side."""
    caller_thread = threading.get_ident()
    source = tmp_path / "cover.jpg"
    source.write_bytes(b"source-cover")
    cache_dir = tmp_path / "cache"
    settings = cover_preview.CoverPreviewSettings(
        enabled=True,
        target_aspect="kobo_libra_color",
        fill_mode="edge_mirror",
        manual_color="#ffffff",
    )
    observed = []

    monkeypatch.setattr(cover_preview, "use_IM", True)
    monkeypatch.setattr(parallel, "run_blocking", _run_on_real_thread)

    def pad_blob(blob, received_settings):
        observed.append((threading.get_ident(), blob, received_settings))
        return b"padded-cover"

    monkeypatch.setattr(cover_preview, "pad_blob", pad_blob)

    target = cover_preview.pad_path_to_cache(
        str(source), str(cache_dir), "padded.jpg", settings,
    )
    assert observed == [(observed[0][0], b"source-cover", settings)]
    assert observed[0][0] != caller_thread
    assert target == str(cache_dir / "padded.jpg")
    assert (cache_dir / "padded.jpg").read_bytes() == b"padded-cover"

    observed.clear()
    hit = cover_preview.pad_path_to_cache(
        str(source), str(cache_dir), "padded.jpg", settings,
    )
    assert hit == target
    assert observed == []
