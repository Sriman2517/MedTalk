import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

CLASSIFIER_PROMPT = """
You are an expert medical triage AI. Read the provided patient medical brief and determine the most appropriate medical specialty for a referral.

You must choose EXACTLY ONE of the following categories:
- General Practice (For mild/unclear issues)
- Cardiology (Heart, blood pressure)
- Dermatology (Skin, hair, nails)
- Orthopedics (Bones, joints, muscles)
- Gynecology/Obstetrics (Pregnancy, female reproductive)
- Pediatrics (Children under 18)
- Gastroenterology (Stomach, digestion)
- Emergency Medicine (Trauma, severe acute pain)

You MUST output your response strictly in JSON format matching this structure:
{
    "recommended_specialty": "The chosen category",
    "search_keyword": "A simple keyword to search on Google Maps (e.g., 'Maternity Hospital', 'Heart Clinic')",
    "confidence_score": 0.0 to 1.0
}
"""


def classify_specialty(medical_brief_json: dict) -> dict:
    """
    Takes the structured medical brief and returns the recommended hospital specialty for the referral engine.
    """
    # Convert the dict back to a string so the LLM can read it
    brief_string = json.dumps(medical_brief_json)

    messages = [
        {"role": "system", "content": CLASSIFIER_PROMPT},
        {"role": "user", "content": f"Classify this patient brief:\n\n{brief_string}"}
    ]

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            temperature=0.0,  # Zero creativity, high precision
            response_format={"type": "json_object"}
        )

        raw_json = response.choices[0].message.content.strip()
        classification = json.loads(raw_json)
        return classification

    except Exception as e:
        print(f"Classification Error: {e}")
        # Fallback to General Practice if the API fails
        return {
            "recommended_specialty": "General Practice",
            "search_keyword": "General Hospital",
            "confidence_score": 0.0
        }


# --- Quick Local Test ---
if __name__ == "__main__":
    # Simulating the output from your summarizer.py
    mock_brief = {
        "chief_complaint": "pain in bottom and stomach",
        "duration": "3 days",
        "severity": "Moderate",
        "associated_symptoms": [],
        "patient_narrative": "The patient reports intermittent lower abdominal and anal pain for three days, worsening at night and persisting even when stationary, without fever, vomiting, blood in stool, or bowel changes. The pain is described as moderate.",
        "red_flags_detected": False
    }

    print("Classifying Referral Specialty...\n")
    result = classify_specialty(mock_brief)

    print(json.dumps(result, indent=4))