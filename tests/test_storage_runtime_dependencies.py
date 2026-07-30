import socket
from io import BytesIO

import boto3
import botocore
from botocore.config import Config
from PIL import Image, features


def _round_trip(image: Image.Image, *, image_format: str, **save_options):
    output = BytesIO()
    image.save(output, format=image_format, **save_options)
    output.seek(0)
    with Image.open(output) as decoded:
        decoded.load()
        return decoded.format, decoded.mode, decoded.size, decoded.getpixel((0, 0))


def test_pillow_runtime_has_required_image_codecs() -> None:
    assert features.check("jpg")
    assert features.check("zlib")
    assert features.check("webp")

    jpeg = _round_trip(
        Image.new("RGB", (2, 2), (12, 34, 56)),
        image_format="JPEG",
        quality=90,
        optimize=True,
        progressive=False,
    )
    assert jpeg[:3] == ("JPEG", "RGB", (2, 2))

    png = _round_trip(
        Image.new("RGBA", (2, 2), (12, 34, 56, 78)),
        image_format="PNG",
        optimize=True,
        compress_level=9,
    )
    assert png == ("PNG", "RGBA", (2, 2), (12, 34, 56, 78))

    webp = _round_trip(
        Image.new("RGBA", (2, 2), (12, 34, 56, 78)),
        image_format="WEBP",
        lossless=True,
        method=6,
    )
    assert webp == ("WEBP", "RGBA", (2, 2), (12, 34, 56, 78))


def test_boto3_client_constructor_uses_no_network(monkeypatch) -> None:
    def reject_network(*_args, **_kwargs) -> None:
        raise AssertionError("boto3 client construction attempted network access")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    client = boto3.client(
        "s3",
        endpoint_url="http://storage.invalid",
        region_name="us-east-1",
        aws_access_key_id="synthetic-access-key",
        aws_secret_access_key="synthetic-secret-key",
        use_ssl=False,
        config=Config(
            signature_version="s3v4",
            connect_timeout=1,
            read_timeout=1,
            retries={"max_attempts": 0, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )

    assert boto3.__version__ == "1.43.59"
    assert botocore.__version__ == "1.43.59"
    assert client.meta.config.signature_version == "s3v4"
    assert client.meta.config.s3 == {"addressing_style": "path"}
