
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
import os

os.environ["INFERENCE_RAM"] = "16"
model_dict = create_model_dict()

config = {
    "force_ocr": True,
    "use_llm": False
}

try:
    converter = PdfConverter(artifact_dict=model_dict, config=config)
    print("Converter initialized with config")
except Exception as e:
    print(f"Error initializing: {e}")
