from app.streaming.stream_profile import default_stream_profile, resolve_stream_profile


def test_default_stream_profile_preserves_existing_runtime_defaults() -> None:
    profile = default_stream_profile()

    assert profile.name is None
    assert profile.webrtc_frame_queue_size == 5
    assert profile.webrtc_target_fps == 30.0
    assert profile.webrtc_video_width == 1920
    assert profile.webrtc_video_height == 1080
    assert profile.cdp_emit_queue_size == 5
    assert profile.screenshot_format == "jpeg"


def test_demo_local_stream_profile_isolated_from_default_path() -> None:
    profile = resolve_stream_profile("demo_local")

    assert profile.name == "demo_local"
    assert profile.device_scale_factor == 2.0
    assert profile.webrtc_frame_queue_size == 1
    assert profile.webrtc_target_fps == 10.0
    assert profile.webrtc_video_width == 1280
    assert profile.webrtc_video_height == 800
    assert profile.cdp_emit_queue_size == 1
    assert profile.preserve_source_dimensions is True
    assert profile.allow_quality_degrade is False
    assert profile.screenshot_format == "png"
