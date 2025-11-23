import json
import os

def parse(file_path):
    """
    Parses the scenario JSON file.

    :param file_path: Path to the .json scenario file
    :return: Dictionary containing the parsed scenario
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Scenario file not found: {file_path}")

    with open(file_path, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in scenario file: {e}")