FILTER_TOOL = {
    "name": "filter_articles",
    "description": "Select the most AI-relevant articles from a list by their indices",
    "input_schema": {
        "type": "object",
        "properties": {
            "selected_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "0-based indices of the selected AI-relevant articles"
            }
        },
        "required": ["selected_indices"]
    }
}