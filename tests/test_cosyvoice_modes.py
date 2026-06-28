import sys
import numpy as np

from worker_runtime.base import SynthRequest
from workers.cosyvoice3.backend import CosyVoice3Backend


class Tensor:
    def __init__(self, value): self.value = np.asarray(value, dtype=np.float32)
    def squeeze(self, axis): return Tensor(np.squeeze(self.value, axis=axis))
    def cpu(self): return self
    def numpy(self): return self.value


class FakeTorch:
    @staticmethod
    def cat(values, dim=0): return Tensor(np.concatenate([v.value for v in values], axis=dim))
    @staticmethod
    def from_numpy(value): return value
    @staticmethod
    def isfinite(value): return np.isfinite(value)


def fake_model():
    calls = []
    def output(name, args):
        calls.append((name, args))
        return iter([{"tts_speech": Tensor(np.ones((1, 2400)) * .05)}])
    model = type("CosyVoice3", (), {})()
    model.inference_zero_shot = lambda *args, **kwargs: output("clone", args)
    model.inference_instruct2 = lambda *args, **kwargs: output("instruct", args)
    model.inference_cross_lingual = lambda *args, **kwargs: output("cross_lingual", args)
    return model, calls


def test_cosyvoice3_dispatches_all_modes_with_prompt_markers(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "torch", FakeTorch)
    audio = tmp_path / "ref.wav"; audio.write_bytes(b"placeholder")
    for mode in ("clone", "instruct", "cross_lingual"):
        backend = CosyVoice3Backend("cosyvoice3", {})
        backend._model, calls = fake_model(); backend._sr = 24000
        result = backend.synthesize(SynthRequest(
            text="你好", voice="qa", language="zh", speed=1.0,
            mode=mode, instruct_text="沉稳", ref_audio_path=str(audio), ref_text="参考文字",
        ))
        assert result[:4] == b"RIFF"
        assert calls[0][0] == mode
        flattened = " ".join(str(value) for value in calls[0][1])
        assert "<|endofprompt|>" in flattened
