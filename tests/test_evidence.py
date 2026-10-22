"""Evidence storage: local copy always, S3 copy and presigned link when S3 is on."""
from app.evidence import EvidenceStore


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kw):
        self.objects[kw["Key"]] = kw["Body"]

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?expires={ExpiresIn}"


def test_local_only(tmp_path):
    out = EvidenceStore(tmp_path).save("d1", "run.json", {"a": 1})
    assert out["s3_key"] is None and out["download_url"] == "/api/v1/evidence/d1/run.json"
    assert (tmp_path / "d1" / "run.json").exists()


def test_s3_copy_and_presigned_link(tmp_path):
    s3 = FakeS3()
    out = EvidenceStore(tmp_path, s3, "bucket", 120).save("d1", "run.txt", "hello")
    assert s3.objects["certification/d1/run.txt"] == b"hello"
    assert out["download_url"] == "https://s3.example/bucket/certification/d1/run.txt?expires=120" and (tmp_path / "d1" / "run.txt").exists()


def test_read_refuses_paths_outside_the_folder(tmp_path):
    (tmp_path / "ev").mkdir()
    (tmp_path / "secret.txt").write_text("x")
    store = EvidenceStore(tmp_path / "ev")
    store.save("d", "a.txt", "ok")
    assert store.read("d/a.txt") is not None
    assert store.read("../secret.txt") is None and store.read("nope") is None
