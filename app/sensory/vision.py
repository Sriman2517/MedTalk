from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


PROMPT = (
    "You are assisting a medical triage system. Describe only visible symptoms or notable "
    "medical-relevant observations in this image using 1-3 short sentences. Do not diagnose. "
    "If the image is unclear, say that the image is unclear."
)

MODEL_CANDIDATES = (
    os.getenv("GEMINI_VISION_MODEL", "").strip(),
    "gemini-2.5-flash",
    "gemini-1.5-pro-latest",
)


def analyze_symptom_image(image_path: str) -> str:
    import google.generativeai as genai
    from PIL import Image

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    genai.configure(api_key=api_key)
    last_error = None

    for model_name in [candidate for candidate in MODEL_CANDIDATES if candidate]:
        try:
            print(f"[vision.analyze_symptom_image] trying model={model_name!r} image_path={image_path!r}", flush=True)
            model = genai.GenerativeModel(model_name)
            with Image.open(path) as image:
                response = model.generate_content([PROMPT, image])
            text = getattr(response, "text", "") or ""
            print(f"[vision.analyze_symptom_image] success model={model_name!r} text={text!r}", flush=True)
            return text.strip()
        except Exception as exc:
            last_error = exc
            print(f"[vision.analyze_symptom_image] failed model={model_name!r} error={exc}", flush=True)

    raise RuntimeError(f"Gemini image analysis failed for all candidate models: {last_error}")
