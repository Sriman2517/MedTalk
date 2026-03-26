import os
import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

# Configure the Gemini API client
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))


def analyze_symptom_image(image_path: str) -> str:
    """
    Passes a patient-uploaded image to Gemini.
    Returns a brief, clinical description of visible symptoms.
    """
    if not os.path.exists(image_path):
        return "Error: Image not found."

    try:
        # Load the image using Pillow (PIL)
        img = Image.open(image_path)

        # We use gemini-2.5-flash as it is lightning fast for multimodal tasks
        model = genai.GenerativeModel('gemini-2.5-flash')

        # The prompt enforces our medical safety guardrails
        prompt = (
            "You are a medical AI assistant. Briefly describe any visible medical symptoms "
            "in this image (e.g., redness, swelling, rash, laceration). "
            "Keep it under 2 sentences. Do NOT diagnose."
        )

        # Gemini takes a list containing both the prompt and the image object
        response = model.generate_content([prompt, img])

        return response.text.strip()

    except Exception as e:
        print(f"Vision API Error: {e}")
        return "Image analysis failed."


# --- Quick Local Test ---
if __name__ == "__main__":
    # Using the exact image name from your terminal output
    test_image = "download.jpeg"

    print(f"Testing Gemini Vision Analysis on {test_image}...\n")
    if os.path.exists(test_image):
        description = analyze_symptom_image(test_image)
        print(f"AI Vision Analysis: '{description}'\n")
        print("(Pass this string to Member 1's summarizer to include in the doctor's brief!)")
    else:
        print(f"Please place an image file named '{test_image}' in this directory to test.")