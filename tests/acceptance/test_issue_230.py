import io
from pathlib import Path
from sc_digger.models import Config
from sc_digger.web.intake import process_uploaded_audio, UploadResult

def test_upload_non_audio_rejected(tmp_path: Path):
    inbox = tmp_path / "inbox"
    intake = tmp_path / "intake"
    inbox.mkdir()
    intake.mkdir()

    cfg = Config({"download": {"inbox_dir": str(inbox), "intake_dir": str(intake)}})
    content = io.BytesIO(b"not an audio file")
    res = process_uploaded_audio(content, "test.txt", cfg)

    assert res.ok is False
    assert res.status == "error"
    assert "Audio" in res.message
