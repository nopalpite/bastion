"""Tests pour map_image.py: validation d'intégrité des images de plan
(n'importe quelle résolution est acceptée, voir le docstring du module) et
lecture de leur ratio réel."""
import io

from PIL import Image

from map_image import DEFAULT_MAP_RATIO, get_image_size, validate_map_image


def _fake_image_stream(width, height, fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="black").save(buf, format=fmt)
    buf.seek(0)
    return buf


def test_accepts_any_resolution():
    assert validate_map_image(_fake_image_stream(1920, 1080)) is None
    assert validate_map_image(_fake_image_stream(800, 600)) is None
    assert validate_map_image(_fake_image_stream(100, 4000)) is None


def test_accepts_jpeg():
    assert validate_map_image(_fake_image_stream(640, 480, fmt="JPEG")) is None


def test_rejects_corrupt_file():
    stream = io.BytesIO(b"not an image, just garbage bytes")
    error = validate_map_image(stream)
    assert error is not None
    assert "illisible" in error or "corrompu" in error


def test_resets_stream_position_after_validation():
    # L'appelant (app.py) doit pouvoir relire/sauvegarder le flux ensuite,
    # que la validation ait échoué ou non.
    stream = _fake_image_stream(800, 600)
    validate_map_image(stream)
    assert stream.tell() == 0

    stream2 = io.BytesIO(b"garbage")
    validate_map_image(stream2)
    assert stream2.tell() == 0


def test_get_image_size_reads_real_resolution(tmp_path):
    path = tmp_path / "plan.png"
    Image.new("RGB", (1280, 720), color="black").save(path)
    assert get_image_size(str(path)) == (1280, 720)


def test_get_image_size_returns_none_for_missing_file(tmp_path):
    assert get_image_size(str(tmp_path / "nope.png")) is None


def test_get_image_size_returns_none_for_corrupt_file(tmp_path):
    path = tmp_path / "bad.png"
    path.write_bytes(b"not a real image")
    assert get_image_size(str(path)) is None


def test_default_map_ratio_is_16_9():
    assert DEFAULT_MAP_RATIO == (16, 9)
