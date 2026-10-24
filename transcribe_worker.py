#!/usr/bin/env python3
import os
import sys
import ctypes
import glob
import site

def _preload_cuda_libs():
    """CTranslate2 dlopens libcublas.so.12/libcudnn*.so.9 by name; pip nvidia-* wheels
    (installed for torch) aren't on the loader path, so load them globally first."""
    for sp in site.getsitepackages():
        for pat in ("nvidia/cublas/lib/libcublasLt.so.*", "nvidia/cublas/lib/libcublas.so.*",
                    "nvidia/cudnn/lib/libcudnn*.so.*"):
            for lib in sorted(glob.glob(os.path.join(sp, pat))):
                try:
                    ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass

def _norm(text):
    return " ".join("".join(ch for ch in text.lower() if ch.isalnum() or ch == " ").split())

_HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "you",
    "subtitles by the amara org community", "please subscribe", "bye"
}

def is_junk_segment(text, avg_logprob, no_speech_prob):
    if avg_logprob < -1.0:
        return True
    if no_speech_prob > 0.6:
        return True
    if _norm(text) in _HALLUCINATIONS:
        return True
    return False

def load_hotwords():
    terms_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terms.txt")
    if not os.path.exists(terms_path):
        return None
    try:
        with open(terms_path, "r", encoding="utf-8") as f:
            terms = [line.strip() for line in f if line.strip()]
        out, n = [], 0
        for t in terms:
            if n + len(t) + 2 > 500:
                break
            out.append(t)
            n += len(t) + 2
        return ", ".join(out) or None
    except Exception as e:
        print(f"Error loading terms.txt: {e}", file=sys.stderr)
        return None

def main():
    if len(sys.argv) < 2:
        print("Usage: transcribe_worker.py <audio.wav>", file=sys.stderr)
        sys.exit(1)

    audio_path = sys.argv[1]
    if not os.path.exists(audio_path):
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    _preload_cuda_libs()
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except ImportError as e:
        print(f"Failed to import faster_whisper / ctranslate2: {e}", file=sys.stderr)
        sys.exit(1)

    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    try:
        model = WhisperModel("large-v3-turbo", device=device, compute_type="int8")
    except Exception as e:
        print(f"Failed to load WhisperModel: {e}", file=sys.stderr)
        sys.exit(1)

    hotwords = load_hotwords()

    try:
        segments, info = model.transcribe(
            audio_path,
            language="en",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            hotwords=hotwords
        )
        texts = []
        for s in segments:
            if is_junk_segment(s.text, s.avg_logprob, s.no_speech_prob):
                continue
            t = s.text.strip()
            if t:
                texts.append(t)
        final_text = " ".join(texts)
        print(final_text, flush=True)
    except Exception as e:
        print(f"Transcription error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
