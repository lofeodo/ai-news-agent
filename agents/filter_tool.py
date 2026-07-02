LANGUAGE_FILTER_TOOL = {
    "name": "filter_by_language",
    "description": "Classify each article as English or French based on a short text sample; omit articles in any other language",
    "input_schema": {
        "type": "object",
        "properties": {
            "articles": {
                "type": "array",
                "description": "Articles that are written in English or French",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "0-based index of the article from the input list"
                        },
                        "language": {
                            "type": "string",
                            "enum": ["en", "fr"],
                            "description": "Detected language of the article"
                        }
                    },
                    "required": ["index", "language"]
                }
            }
        },
        "required": ["articles"]
    }
}

FILTER_TOOL = {
    "name": "filter_articles",
    "description": "Filter a list of news articles for AI relevance and assign each a category",
    "input_schema": {
        "type": "object",
        "properties": {
            "articles": {
                "type": "array",
                "description": "List of selected articles with their index and assigned category",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "0-based index of the article from the input list"
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "Model & Product Releases",
                                "Industry & Business",
                                "Policy, Law & Regulation",
                                "Open Source & Tools",
                                "Safety & Alignment",
                                "Society & Culture",
                                "Canada & Montreal"
                            ],
                            "description": "The most appropriate category for this article"
                        }
                    },
                    "required": ["index", "category"]
                }
            }
        },
        "required": ["articles"]
    }
}